"""Result poller: don't wait for clinics to push — go and look.

For every open follow-up that came from the EHR, re-query the resources it
points at (orders, appointments, pending reports) and anything based on
them. New versions go through the normal clinic-evidence intake
(``integrations._ingest_one``), so the product rules are unchanged: a final
result queues a one-click, evidence-backed approval; it never closes a loop
by itself. Already-processed versions are recognised as duplicates.

Lookups are batched: references are de-duplicated across follow-ups, the
``based-on`` searches carry up to ``BATCH`` order references each (comma =
OR in FHIR search), direct references are fetched with one ``_id`` search per
type and batch, and the requests run concurrently. Every resource from a
batched search is attributed back to the references it actually cites, and
results are applied in the same order as one-reference-at-a-time lookups. A
server that rejects a batched search is asked one reference at a time.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from pymongo.database import Database

from ..engine import OPEN
from ..util import new_id, utcnow
from .fhir_client import FHIRClient, FHIRError
from .pull import normalize_reference, version_ref

log = logging.getLogger("continuum.ingest.poller")

ACTOR = "EHR result poller"
BASED_ON_SEARCHES = ("DiagnosticReport", "Appointment", "Encounter")
DIRECT_TYPES = {"DiagnosticReport", "Appointment", "Encounter"}
BATCH = 25
WORKERS = 8


def _related(client: FHIRClient, reference: str) -> list[dict]:
    """Resources related to one reference (the unbatched lookup)."""
    rtype, _, rid = reference.partition("/")
    if not rid:
        return []
    out: list[dict] = []
    if rtype == "ServiceRequest":
        for target in BASED_ON_SEARCHES:
            out.extend(_based_on_one(client, target, reference))
    elif rtype in DIRECT_TYPES:
        found = client.read(rtype, rid)
        if found:
            out.append(found)
    return out


def _based_on_one(client: FHIRClient, target: str, reference: str) -> list[dict]:
    try:
        return client.search(target, {"based-on": reference}, max_pages=1)
    except FHIRError as exc:
        # Not every server supports based-on for every type.
        log.debug("based-on search on %s failed: %s", target, exc)
        return []


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _based_on_batch(client: FHIRClient, target: str, refs: list[str]) -> dict[str, list[dict]]:
    """``target?based-on=a,b,c`` → {order ref: [resources citing it]}."""
    wanted = set(refs)
    try:
        hits = client.search(target, {"based-on": ",".join(refs), "_count": 100}, max_pages=10)
    except FHIRError as exc:
        log.debug("Batched based-on search on %s failed (%s); one at a time.", target, exc)
        return {r: _based_on_one(client, target, r) for r in refs}
    out: dict[str, list[dict]] = {r: [] for r in refs}
    for resource in hits:
        cited = {normalize_reference(b.get("reference")) for b in resource.get("basedOn") or []}
        for r in refs:
            if r in cited and r in wanted:
                out[r].append(resource)
    return out


def _read_batch(client: FHIRClient, rtype: str, refs: list[str]) -> dict[str, list[dict] | FHIRError]:
    """``rtype?_id=a,b,c`` → {ref: [current version]}; per-id reads if refused."""
    ids = [r.partition("/")[2] for r in refs]
    try:
        hits = client.search(rtype, {"_id": ",".join(ids), "_count": len(ids)}, max_pages=2)
    except FHIRError as exc:
        log.debug("Batched _id search on %s failed (%s); reading one at a time.", rtype, exc)
        out: dict[str, list[dict] | FHIRError] = {}
        for r, rid in zip(refs, ids):
            try:
                found = client.read(rtype, rid)
                out[r] = [found] if found else []
            except FHIRError as read_exc:
                out[r] = read_exc
        return out
    by_id = {h.get("id"): h for h in hits}
    return {r: ([by_id[rid]] if rid in by_id else []) for r, rid in zip(refs, ids)}


def fetch_related(client: FHIRClient, references: list[str]) -> dict[str, list[dict] | FHIRError]:
    """Related resources for many references at once, keyed by reference.

    Same result per reference as ``_related``: for an order, the reports,
    appointments and encounters based on it (in that order); for a report,
    appointment or encounter, its current version. A failed direct read is
    returned as the ``FHIRError`` so the caller can report it.
    """
    refs = list(dict.fromkeys(r for r in references if r.partition("/")[2]))
    orders = [r for r in refs if r.startswith("ServiceRequest/")]
    direct: dict[str, list[str]] = {}
    for r in refs:
        rtype = r.partition("/")[0]
        if rtype in DIRECT_TYPES:
            direct.setdefault(rtype, []).append(r)

    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="fhir-poll") as pool:
        based = {
            (target, i): pool.submit(_based_on_batch, client, target, chunk)
            for target in BASED_ON_SEARCHES
            for i, chunk in enumerate(_chunks(orders, BATCH))
        }
        reads = [pool.submit(_read_batch, client, rtype, chunk) for rtype, rs in direct.items() for chunk in _chunks(rs, BATCH)]
        by_target: dict[str, dict[str, list[dict]]] = {t: {} for t in BASED_ON_SEARCHES}
        for (target, _), future in based.items():
            by_target[target].update(future.result())
        out: dict[str, list[dict] | FHIRError] = {}
        for future in reads:
            out.update(future.result())
    for r in orders:
        out[r] = [res for target in BASED_ON_SEARCHES for res in by_target[target].get(r, [])]
    return {r: out.get(r, []) for r in refs}


def poll_ehr(db: Database, client: FHIRClient, *, limit: int = 200) -> dict:
    from ..integrations import _ingest_one  # shares matching + closure rules with clinic push

    query = {"fhir_refs": {"$exists": True, "$ne": []}, "status": {"$in": sorted(OPEN)}}
    obligations = list(db.obligations.find(query, {"_id": 0, "id": 1, "fhir_refs": 1}).limit(limit))
    seen: set[str] = set()
    results: list[dict] = []
    errors: list[str] = []
    references = [
        normalize_reference(reference) or reference
        for obligation in obligations
        for reference in obligation.get("fhir_refs") or []
    ]
    related_by_ref = fetch_related(client, references)
    for reference in references:
        related = related_by_ref.get(reference, [])
        if isinstance(related, FHIRError):
            errors.append(f"{reference}: {related}")
            continue
        for resource in related:
            key = version_ref(resource)
            if key in seen:
                continue
            seen.add(key)
            results.append(_ingest_one(db, resource, ACTOR))
    applied = [r for r in results if r["outcome"] not in {"ignored", "duplicate", "unmatched"}]
    run = {
        "id": new_id("poll"),
        "at": utcnow(),
        "checked_obligations": len(obligations),
        "resources_seen": len(seen),
        "applied": len(applied),
        "errors": errors[:20],
    }
    db.ehr_poll_runs.insert_one(dict(run))
    if applied:
        log.info("EHR poll applied %d update(s).", len(applied))
    return {**run, "results": applied}
