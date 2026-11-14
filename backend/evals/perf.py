"""Performance benchmark: ingestion, result polling and the hot API endpoints.

    cd backend
    .venv/bin/python -m evals.perf --label before
    .venv/bin/python -m evals.perf --label after
    .venv/bin/python -m evals.perf --compare before after     # markdown table

Runs in-process against a real MongoDB (Atlas Local by default) and a real
FHIR server (the HAPI sandbox loaded by ``scripts/ehr_sandbox``), so the
numbers include network round trips. It never writes to the FHIR server.

For every operation it records wall time, MongoDB commands (one command =
one round trip; ``getMore`` included, server heartbeats excluded) and FHIR
HTTP requests. Ingestion is broken down per job step.

Environment:

* ``PERF_MONGODB_URI``  default ``mongodb://continuum:localtest@localhost:27018/?directConnection=true``
* ``PERF_MONGODB_DB``   default ``continuum_perf`` (dropped at the start of every run)
* ``PERF_FHIR_BASE``    default ``http://localhost:8080/fhir``
* ``PERF_MANIFEST``     default ``scripts/ehr_sandbox/.manifest.json``
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"

os.environ["CONTINUUM_DB_MODE"] = "atlas"
os.environ["MONGODB_URI"] = os.environ.get(
    "PERF_MONGODB_URI", "mongodb://continuum:localtest@localhost:27018/?directConnection=true"
)
os.environ["MONGODB_DB"] = os.environ.get("PERF_MONGODB_DB", "continuum_perf")
os.environ["EHR_FHIR_BASE_URL"] = os.environ.get("PERF_FHIR_BASE", "http://localhost:8080/fhir")
os.environ["AUTO_SEED"] = "true"
os.environ["DEMO_MODE"] = "true"
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
os.environ["HL7_MLLP_PORT"] = "0"
os.environ["LLM_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("INTEGRATION_SECRET", "perf-integration-secret")

from pymongo import monitoring  # noqa: E402


class _MongoCounter(monitoring.CommandListener):
    IGNORE = {"hello", "ismaster", "isMaster", "ping", "buildInfo", "endSessions", "saslStart", "saslContinue"}

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_name: Counter = Counter()
        self.by_target: Counter = Counter()

    def started(self, event) -> None:
        if event.command_name in self.IGNORE:
            return
        target = event.command.get(event.command_name)
        with self.lock:
            self.by_name[event.command_name] += 1
            self.by_target[f"{event.command_name} {target if isinstance(target, str) else ''}".strip()] += 1

    def succeeded(self, event) -> None:
        pass

    def failed(self, event) -> None:
        pass


MONGO = _MongoCounter()
monitoring.register(MONGO)

import httpx  # noqa: E402

FHIR_BASE = os.environ["EHR_FHIR_BASE_URL"].rstrip("/")


class _FHIRCounter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_type: Counter = Counter()
        self.ms_by_type: Counter = Counter()

    @staticmethod
    def kind(url: str) -> str | None:
        if not url.startswith(FHIR_BASE):
            return None
        # HAPI paging links are "<base>?_getpages=…": count them as pages.
        return url[len(FHIR_BASE):].lstrip("/").split("?")[0].split("/")[0] or "(next page)"

    def hit(self, url: str, ms: float) -> None:
        rtype = self.kind(url)
        if rtype is None:
            return
        with self.lock:
            self.by_type[rtype] += 1
            self.ms_by_type[rtype] += ms


FHIR = _FHIRCounter()
_orig_send = httpx.Client.send


def _counting_send(self, request, *args, **kwargs):
    t0 = time.perf_counter()
    try:
        return _orig_send(self, request, *args, **kwargs)
    finally:
        FHIR.hit(str(request.url), (time.perf_counter() - t0) * 1000)


httpx.Client.send = _counting_send  # type: ignore[method-assign]

sys.path.insert(0, str(ROOT / "backend"))

from app.db import backfill_embeddings, bootstrap, demo_today, ensure_vector_index, get_client, get_db  # noqa: E402
from app.ingest import jobs as jobs_mod  # noqa: E402
from app.ingest.fhir_client import client_from_settings  # noqa: E402
from app.ingest.hl7 import build_a03  # noqa: E402
from app.ingest.orders import ensure_order_index  # noqa: E402
from app.ingest.poller import poll_ehr  # noqa: E402
from app.ingest.service import handle_hl7  # noqa: E402


# ── measurement helpers ──────────────────────────────────────────────────


def _snapshot() -> tuple[int, int]:
    return sum(MONGO.by_name.values()), sum(FHIR.by_type.values())


@contextmanager
def measure(out: dict):
    m0, f0 = _snapshot()
    mn0, mt0, ft0, fms0 = Counter(MONGO.by_name), Counter(MONGO.by_target), Counter(FHIR.by_type), Counter(FHIR.ms_by_type)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        m1, f1 = _snapshot()
        out["mongo"] = m1 - m0
        out["fhir"] = f1 - f0
        out["mongo_by_command"] = dict(MONGO.by_name - mn0)
        out["mongo_by_target"] = dict(MONGO.by_target - mt0)
        out["fhir_by_type"] = dict(FHIR.by_type - ft0)
        # Summed request time; exceeds wall time when requests run concurrently.
        out["fhir_ms_by_type"] = {k: round(v, 1) for k, v in (FHIR.ms_by_type - fms0).items()}


STEP_STATS: list[dict] = []
_orig_run_step = jobs_mod._run_step


def _timed_run_step(db, job, step, client):
    stat: dict = {"step": step}
    with measure(stat):
        result = _orig_run_step(db, job, step, client)
    STEP_STATS.append(stat)
    return result


jobs_mod._run_step = _timed_run_step


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 1) if values else 0.0


# ── scenario ─────────────────────────────────────────────────────────────


def _manifest() -> list[dict]:
    path = Path(os.environ.get("PERF_MANIFEST", ROOT / "scripts/ehr_sandbox/.manifest.json"))
    if not path.exists():
        sys.exit(f"No sandbox manifest at {path}; load the HAPI sandbox first (scripts/ehr_sandbox).")
    return json.loads(path.read_text())


def _a03(p: dict) -> str:
    discharge = date.fromisoformat(p["discharge"])
    return build_a03(
        control_id=f"ADT{uuid.uuid4().hex[:10].upper()}",
        mrn=p["mrn"], family=p["family"], given=p["given"], birth_date=p["birth_date"], sex=p["sex"],
        visit_number=p["visit_number"], admit=(discharge - timedelta(days=4)).isoformat(), discharge=p["discharge"],
        attending="Dr. Priya Raman", phone=p["phone"],
    )


def _setup() -> dict:
    out: dict = {}
    client = get_client()
    client.drop_database(os.environ["MONGODB_DB"])
    with measure(out):
        bootstrap()  # validators, indexes, demo users, seed
        from app.agent.llm import get_embeddings

        get_embeddings()  # load the local model outside the timed ingest
        backfill_embeddings()
        ensure_vector_index()
        ensure_order_index(get_db())
    # Wait for the vector indexes so ingest takes the same path every run.
    deadline = time.time() + 120
    from app.db import vector_search_available
    from app.ingest.orders import _vector_ready

    while time.time() < deadline and not (vector_search_available() and _vector_ready(get_db())):
        time.sleep(1)
    out["vector_ready"] = vector_search_available()
    out["order_vector_ready"] = _vector_ready(get_db())
    return out


def _ingest_once(p: dict, fhir, **extra) -> dict:
    STEP_STATS.clear()
    stat: dict = {"patient": p["n"], **extra}
    with measure(stat):
        result = handle_hl7(_a03(p), client=fhir)
    stat["outcome"] = result.get("outcome")
    stat["detail"] = result.get("detail")
    stat["steps"] = list(STEP_STATS)
    return stat


def bench_ingest(patients: list[dict], rounds: int = 1) -> dict:
    """First discharge of each sandbox patient (``rounds`` times, each on a
    fresh database), then one re-send of the same stay (idempotent path)."""
    fhir = client_from_settings()
    runs = []
    for r in range(rounds):
        if r:
            _setup()
        runs += [_ingest_once(p, fhir, round=r) for p in patients]
    # Same patient, new control id → new job against the existing episode.
    again = _ingest_once(patients[0], fhir, reingest=True)
    fhir.close()

    steps: dict = {}
    for name in jobs_mod.STEPS:
        rows = [s for r in runs for s in r["steps"] if s["step"] == name]
        steps[name] = {
            "ms": _median([s["ms"] for s in rows]),
            "mongo": _median([s["mongo"] for s in rows]),
            "fhir": _median([s["fhir"] for s in rows]),
        }
    return {
        "first_ingest": {
            "ms": _median([r["ms"] for r in runs]),
            "max_ms": max(r["ms"] for r in runs),
            "mongo": _median([r["mongo"] for r in runs]),
            "fhir": _median([r["fhir"] for r in runs]),
            "steps": steps,
            "runs": runs,
        },
        "reingest": again,
    }


def _fan_out_followups(target: int) -> int:
    """Add synthetic EHR-backed follow-ups until ``target`` are open.

    Clones keep the shape of the real ones (same mix of ServiceRequest /
    Appointment / DiagnosticReport references) but point at distinct ids that
    don't exist in the sandbox, so every follow-up costs the poller real
    lookups, as distinct patients would.
    """
    db = get_db()
    base = list(db.obligations.find({"fhir_refs": {"$exists": True, "$ne": []}}, {"_id": 0}))
    if not base:
        return 0
    have = len(base)
    docs = []
    i = 0
    while have + len(docs) < target:
        src = dict(base[i % len(base)])
        src["id"] = f"obl_perf_{i}"
        src["patient_id"] = f"pat_perf_{i // len(base)}"
        src["fhir_refs"] = [f"{r.split('/')[0]}/perf-{i}-{j}" for j, r in enumerate(src["fhir_refs"])]
        docs.append(src)
        i += 1
    if docs:
        db.obligations.insert_many(docs)
    return have + len(docs)


def bench_poller(target_followups: int, repeats: int) -> dict:
    total = _fan_out_followups(target_followups)
    fhir = client_from_settings()
    runs = []
    for _ in range(repeats):
        stat: dict = {}
        with measure(stat):
            result = poll_ehr(get_db(), fhir, limit=max(200, target_followups))
        stat["checked"] = result["checked_obligations"]
        stat["resources_seen"] = result["resources_seen"]
        stat["applied"] = result["applied"]
        stat["errors"] = len(result["errors"])
        runs.append(stat)
    fhir.close()
    return {
        "followups": total,
        "ms": _median([r["ms"] for r in runs]),
        "mongo": _median([r["mongo"] for r in runs]),
        "fhir": _median([r["fhir"] for r in runs]),
        "runs": runs,
    }


def bench_api(repeats: int) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    db = get_db()
    out: dict = {}
    with TestClient(app) as tc:
        creds = tc.get("/api/auth/demo").json()["coordinator"]
        token = tc.post("/api/auth/login", json={"email": creds["email"], "password": creds["password"]}).json()["token"]
        tc.headers["Authorization"] = f"Bearer {token}"
        # The patient with the most follow-ups is the heaviest journey.
        top = list(db.obligations.aggregate([
            {"$match": {"patient_id": {"$not": {"$regex": "^pat_perf_"}}}},
            {"$group": {"_id": "$patient_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 1},
        ]))
        pid = top[0]["_id"]
        endpoints = {
            "GET /api/dashboard": "/api/dashboard",
            "GET /api/patients": "/api/patients",
            "GET /api/patients/{id}/journey": f"/api/patients/{pid}/journey",
            "GET /api/agent/approvals": "/api/agent/approvals",
        }
        for label, path in endpoints.items():
            tc.get(path)  # warm
            rows = []
            for _ in range(repeats):
                stat: dict = {}
                with measure(stat):
                    response = tc.get(path)
                assert response.status_code == 200, (path, response.status_code, response.text[:200])
                stat["bytes"] = len(response.content)
                rows.append(stat)
            out[label] = {
                "ms": _median([r["ms"] for r in rows]),
                "mongo": _median([r["mongo"] for r in rows]),
                "bytes": rows[-1]["bytes"],
                "mongo_by_command": rows[-1]["mongo_by_command"],
            }
        out["_dataset"] = {
            "patients": db.patients.count_documents({}),
            "obligations": db.obligations.count_documents({}),
            "evidence": db.evidence.count_documents({}),
            "journey_patient_obligations": top[0]["n"],
        }
    return out


# ── report ───────────────────────────────────────────────────────────────


def _rows(r: dict) -> list[tuple[str, dict]]:
    fi = r["ingest"]["first_ingest"]
    rows = [("Ingest (ADT^A03 → verified), median", fi)]
    rows += [(f"  · step {name}", fi["steps"][name]) for name in jobs_mod.STEPS]
    rows.append(("Re-ingest same stay (idempotent)", r["ingest"]["reingest"]))
    rows.append((f"Result poll, {r['poller']['followups']} open follow-ups", r["poller"]))
    rows += [(k, v) for k, v in r["api"].items() if not k.startswith("_")]
    return rows


def print_table(r: dict) -> None:
    print(f"\n{'operation':<46} {'ms':>9} {'mongo':>7} {'fhir':>6}")
    for label, v in _rows(r):
        print(f"{label:<46} {v['ms']:>9} {v.get('mongo', ''):>7} {v.get('fhir', ''):>6}")


def compare(before: dict, after: dict) -> str:
    lines = [
        "| Operation | Before ms | After ms | Speed-up | Mongo round trips (before → after) | FHIR requests (before → after) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    after_rows = dict(_rows(after))
    for label, b in _rows(before):
        a = after_rows.get(label)
        if a is None:
            continue
        speed = f"{b['ms'] / a['ms']:.1f}×" if a["ms"] else "—"
        fhir = f"{b.get('fhir', '—')} → {a.get('fhir', '—')}" if "fhir" in b else "—"
        lines.append(f"| {label.strip()} | {b['ms']} | {a['ms']} | {speed} | {b['mongo']} → {a['mongo']} | {fhir} |")
    return "\n".join(lines)


def merge(reports: list[dict]) -> dict:
    """Pool several runs of one code version: ingest medians over every
    ingest, everything else the median of the per-run medians."""
    if len(reports) == 1:
        return reports[0]
    runs = [r for rep in reports for r in rep["ingest"]["first_ingest"]["runs"]]
    steps = {
        name: {k: _median([s[k] for r in runs for s in r["steps"] if s["step"] == name]) for k in ("ms", "mongo", "fhir")}
        for name in jobs_mod.STEPS
    }
    def med(rows: list[dict]) -> dict:
        return {k: _median([r[k] for r in rows]) for k in ("ms", "mongo", "fhir") if k in rows[0]}
    api_keys = [k for k in reports[0]["api"] if not k.startswith("_")]
    return {
        "label": "+".join(r["label"] for r in reports),
        "ingest": {
            "first_ingest": {**{k: _median([r[k] for r in runs]) for k in ("ms", "mongo", "fhir")},
                             "max_ms": max(r["ms"] for r in runs), "steps": steps, "runs": runs},
            "reingest": med([rep["ingest"]["reingest"] for rep in reports]),
        },
        "poller": {"followups": reports[0]["poller"]["followups"], **med([rep["poller"] for rep in reports])},
        "api": {k: med([rep["api"][k] for rep in reports]) for k in api_keys},
    }


def _load(labels: str) -> dict:
    return merge([json.loads((RESULTS_DIR / f"perf-{x}.json").read_text()) for x in labels.split(",")])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="run")
    parser.add_argument("--followups", type=int, default=150, help="open follow-ups for the poller benchmark")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=3, help="fresh-database ingest rounds over the sandbox patients")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="labels of saved runs; comma-separate several runs of one version to pool them")
    parser.add_argument("--profile", action="store_true", help="cProfile one warm ingest and print the top functions")
    args = parser.parse_args()

    if args.compare:
        b, a = (_load(x) for x in args.compare)
        print(compare(b, a))
        print(f"\nIngest runs pooled: {len(b['ingest']['first_ingest']['runs'])} before, "
              f"{len(a['ingest']['first_ingest']['runs'])} after; slowest {b['ingest']['first_ingest']['max_ms']} ms → "
              f"{a['ingest']['first_ingest']['max_ms']} ms")
        return

    patients = _manifest()
    print(f"MongoDB {os.environ['MONGODB_DB']} · FHIR {FHIR_BASE} · {len(patients)} sandbox patients")
    report = {"label": args.label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "setup": _setup()}
    print(f"setup {report['setup']}")
    if args.profile:
        import cProfile
        import pstats

        bench_ingest(patients[:1])  # warm caches and models
        get_client().drop_database(os.environ["MONGODB_DB"])
        _setup()
        prof = cProfile.Profile()
        prof.enable()
        bench_ingest(patients[1:2])
        prof.disable()
        pstats.Stats(prof).sort_stats("cumulative").print_stats(45)
        return
    report["ingest"] = bench_ingest(patients, rounds=args.rounds)
    for run in report["ingest"]["first_ingest"]["runs"] + [report["ingest"]["reingest"]]:
        print(f"  patient {run['patient']}{' (re-ingest)' if run.get('reingest') else ''}: {run['ms']} ms, "
              f"{run['mongo']} mongo, {run['fhir']} fhir — {run['outcome']}: {run['detail']}")
    report["poller"] = bench_poller(args.followups, max(2, args.repeats // 2))
    report["api"] = bench_api(args.repeats)
    print_table(report)
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"perf-{args.label}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
