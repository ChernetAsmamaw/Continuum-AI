"""Scoring. Each function returns a score in [0, 1], or None when the case
doesn't exercise that metric."""

from __future__ import annotations

from datetime import date

from .systems import render

# metric key -> (label, target for Continuum, higher is better)
METRICS = {
    "extraction_recall": ("Follow-ups extracted from discharge text (recall)", 0.95),
    "extraction_precision": ("Extracted follow-ups that were really ordered (precision)", 0.90),
    "loop_state_accuracy": ("Follow-ups in the correct state at the end", 0.90),
    "open_loops_retained": ("Still-open follow-ups the system still tracks", 1.0),
    "no_unverified_closure": ("Patient self-reports NOT treated as proof", 1.0),
    "contradiction_caught": ("Seeded date contradictions caught and preserved", 1.0),
    "escalation_correct": ("Clinical/medication questions escalated, others not", 1.0),
    "date_accuracy": ("Clinic-confirmed dates remembered", 1.0),
}


def _matches(actual: dict | None, expected) -> bool:
    allowed = expected if isinstance(expected, list) else [expected]
    if actual is None:
        return "absent" in allowed
    return actual.get("status") in allowed


def score(outputs: dict, expect: dict) -> dict[str, float | None]:
    state: dict = outputs.get("obligations") or {}
    anchor = date.fromisoformat(outputs["anchor"])
    out: dict[str, float | None] = {k: None for k in METRICS}

    cats = expect.get("categories")
    if cats:
        found = set(state)
        expected = set(cats)
        out["extraction_recall"] = len(found & expected) / len(expected)
        out["extraction_precision"] = (len(found & expected) / len(found)) if found else 0.0

    obligations = expect.get("obligations") or {}
    if obligations:
        hits = [_matches(state.get(cat), exp) for cat, exp in obligations.items()]
        out["loop_state_accuracy"] = sum(hits) / len(hits)
        still_open = [c for c, e in obligations.items() if e == "open"]
        if still_open and len(expect.get("categories") or []) == 0:
            tracked = [c for c in still_open if state.get(c) and state[c]["status"] not in {"closed", "cancelled"}]
            out["open_loops_retained"] = len(tracked) / len(still_open)

    reported = expect.get("self_reported") or []
    if reported:
        closed = [c for c in reported if (state.get(c) or {}).get("status") == "closed"]
        out["no_unverified_closure"] = 1 - len(closed) / len(reported)

    conflicts = expect.get("conflicts") or []
    if conflicts:
        caught = [c for c in conflicts if (state.get(c) or {}).get("status") == "conflict"]
        out["contradiction_caught"] = len(caught) / len(conflicts)

    if "escalated" in expect:
        out["escalation_correct"] = 1.0 if bool(outputs.get("escalated")) == expect["escalated"] else 0.0

    dates = expect.get("dates") or {}
    if dates:
        ok = [(state.get(c) or {}).get("date") == render(d, anchor) for c, d in dates.items()]
        out["date_accuracy"] = sum(ok) / len(ok)

    return out


def aggregate(per_case: list[dict[str, float | None]]) -> dict[str, dict]:
    summary = {}
    for key in METRICS:
        vals = [c[key] for c in per_case if c.get(key) is not None]
        summary[key] = {"mean": (sum(vals) / len(vals)) if vals else None, "n": len(vals)}
    return summary
