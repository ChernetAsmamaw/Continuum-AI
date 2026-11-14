"""Run the Continuum evaluation suite.

    cd backend
    python -m evals.run                 # offline: prints a table, writes evals/results/
    python -m evals.run --langsmith     # also uploads the dataset and both experiments

The suite always runs against an in-memory MongoDB (mongomock) so it never
touches your Atlas data. LLM settings come from your .env as usual: with an
LLM key both systems use that model; without one both use the deterministic
extractor. ``--langsmith`` needs LANGCHAIN_API_KEY (or LANGSMITH_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Isolate the run before the app reads its settings.
os.environ["CONTINUUM_DB_MODE"] = "memory"
os.environ.setdefault("MONGODB_DB", "continuum_eval")
os.environ["AUTO_SEED"] = "false"
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
os.environ["ELEVENLABS_API_KEY"] = ""

from .dataset import CASES  # noqa: E402
from .metrics import METRICS, aggregate, score  # noqa: E402
from .systems import run_baseline, run_continuum  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATASET_NAME = "continuum-care-loops"
SYSTEMS = {"continuum": run_continuum, "stateless_baseline": run_baseline}


def _inputs(case: dict) -> dict:
    return {"case_id": case["id"], "group": case["group"], "steps": case["steps"]}


def run_offline(cases: list[dict]) -> dict:
    report: dict = {"cases": [], "summary": {}}
    per_system: dict[str, list] = {name: [] for name in SYSTEMS}
    for case in cases:
        row = {"id": case["id"], "group": case["group"], "description": case["description"], "systems": {}}
        for name, fn in SYSTEMS.items():
            outputs = fn(_inputs(case))
            scores = score(outputs, case["expect"])
            per_system[name].append(scores)
            row["systems"][name] = {"scores": scores, "outputs": outputs}
        report["cases"].append(row)
    report["summary"] = {name: aggregate(rows) for name, rows in per_system.items()}
    return report


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def render_markdown(report: dict, meta: dict) -> str:
    c, b = report["summary"]["continuum"], report["summary"]["stateless_baseline"]
    lines = [
        "# Continuum evaluation results",
        "",
        f"Run {meta['ran_at']} · {meta['cases']} cases · extractor: `{meta['extractor']}`",
        "",
        "Continuum (memory + Unclosed Loop Engine) against a stateless baseline: the same",
        "extractor with no memory and no rules, seeing only the latest conversation.",
        "",
        "| Metric | Target | Continuum | Stateless baseline | Cases |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, (label, target) in METRICS.items():
        lines.append(
            f"| {label} | {_pct(target)} | **{_pct(c[key]['mean'])}** | {_pct(b[key]['mean'])} | {c[key]['n']} |"
        )
    lines += ["", "## Per case", "", "| Case | Continuum | Baseline | Misses (Continuum) |", "| --- | --- | --- | --- |"]
    for row in report["cases"]:
        def avg(name: str) -> float | None:
            vals = [v for v in row["systems"][name]["scores"].values() if v is not None]
            return sum(vals) / len(vals) if vals else None

        misses = [k for k, v in row["systems"]["continuum"]["scores"].items() if v is not None and v < 1]
        lines.append(
            f"| `{row['id']}` | {_pct(avg('continuum'))} | {_pct(avg('stateless_baseline'))} | {', '.join(misses) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def print_table(report: dict) -> None:
    c, b = report["summary"]["continuum"], report["summary"]["stateless_baseline"]
    width = max(len(label) for label, _ in METRICS.values())
    print(f"\n{'Metric'.ljust(width)}  Target  Continuum  Baseline   n")
    for key, (label, target) in METRICS.items():
        print(
            f"{label.ljust(width)}  {_pct(target):>6}  {_pct(c[key]['mean']):>9}  {_pct(b[key]['mean']):>8}  {c[key]['n']:>2}"
        )


def run_langsmith(cases: list[dict], extractor: str) -> list[str]:
    from langsmith import Client

    client = Client()
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        for example in client.list_examples(dataset_id=dataset.id):
            client.delete_example(example.id)
    else:
        dataset = client.create_dataset(
            DATASET_NAME,
            description="Synthetic post-discharge care-loop scenarios: extraction, self-reports, "
            "contradictions, escalation and multi-session memory.",
        )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": _inputs(c),
                "outputs": c["expect"],
                "metadata": {"description": c["description"], "group": c["group"]},
            }
            for c in cases
        ],
    )

    def evaluator(outputs: dict, reference_outputs: dict) -> dict:
        scores = score(outputs, reference_outputs)
        return {"results": [{"key": k, "score": v} for k, v in scores.items() if v is not None]}

    urls = []
    for name, fn in SYSTEMS.items():
        results = client.evaluate(
            fn,
            data=DATASET_NAME,
            evaluators=[evaluator],
            experiment_prefix=name,
            metadata={"system": name, "extractor": extractor},
            max_concurrency=1,
        )
        url = getattr(results, "url", None) or getattr(getattr(results, "_manager", None), "url", None)
        if url:
            urls.append(url)
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--langsmith", action="store_true", help="upload the dataset and run both experiments in LangSmith")
    parser.add_argument("--case", action="append", help="run only these case ids")
    parser.add_argument("--no-write", action="store_true", help="don't write evals/results/")
    args = parser.parse_args(argv)

    from app.agent.llm import llm_available
    from app.config import settings

    extractor = f"llm:{settings.llm_model}" if llm_available() else "heuristic (no LLM key)"
    cases = [c for c in CASES if not args.case or c["id"] in args.case]
    report = run_offline(cases)
    meta = {
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "cases": len(cases),
        "extractor": extractor,
    }
    report["meta"] = meta
    print_table(report)

    if not args.no_write:
        RESULTS_DIR.mkdir(exist_ok=True)
        (RESULTS_DIR / "latest.json").write_text(json.dumps(report, indent=2, default=str))
        (RESULTS_DIR / "latest.md").write_text(render_markdown(report, meta))
        print(f"\nWrote {RESULTS_DIR / 'latest.md'}")

    if args.langsmith:
        if not (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY") or settings.tracing_key):
            print("\n--langsmith needs LANGSMITH_API_KEY (or LANGCHAIN_API_KEY).", file=sys.stderr)
            return 2
        if settings.tracing_key:
            os.environ.setdefault("LANGSMITH_API_KEY", settings.tracing_key)
        for url in run_langsmith(cases, extractor):
            print(f"LangSmith experiment: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
