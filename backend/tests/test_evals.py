"""The evaluation suite doubles as a regression test: Continuum must meet the
targets on every metric, and the stateless baseline must not."""

from evals.dataset import CASES
from evals.metrics import METRICS, score
from evals.run import run_offline


def test_continuum_meets_every_target_and_beats_the_baseline():
    report = run_offline(CASES)
    continuum = report["summary"]["continuum"]
    baseline = report["summary"]["stateless_baseline"]
    for key, (label, target) in METRICS.items():
        assert continuum[key]["n"] > 0, f"no case exercises {key}"
        assert continuum[key]["mean"] >= target, f"{label}: {continuum[key]['mean']:.2f} < {target}"
    # Memory and rules are what the baseline lacks.
    for key in ("loop_state_accuracy", "open_loops_retained", "contradiction_caught", "no_unverified_closure"):
        assert baseline[key]["mean"] < continuum[key]["mean"], key
    for row in report["cases"]:
        assert not row["systems"]["continuum"]["outputs"]["errors"], row["id"]


def test_scoring_treats_absent_and_alternatives():
    outputs = {
        "anchor": "2026-11-13",
        "obligations": {"cardiology": {"status": "scheduled", "date": "2026-11-25"}},
        "escalated": False,
    }
    expect = {
        "obligations": {"cardiology": ["scheduled", "conflict"], "home_care": "absent"},
        "dates": {"cardiology": "{iso+12}"},
        "escalated": True,
    }
    s = score(outputs, expect)
    assert s["loop_state_accuracy"] == 1.0
    assert s["date_accuracy"] == 1.0
    assert s["escalation_correct"] == 0.0
    assert s["extraction_recall"] is None
