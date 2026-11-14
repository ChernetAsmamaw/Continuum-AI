# Evaluation

Continuum ships with an evaluation suite that measures the claims the product
makes, and compares Continuum with a **stateless baseline**: the same
extraction model with no memory and no Unclosed Loop Engine, seeing only the
latest conversation or document, the way a plain chat assistant would.

```bash
cd backend
python -m evals.run              # offline, ~5 s, writes evals/results/latest.md
python -m evals.run --langsmith  # also uploads the dataset and both experiments
```

The latest results are in [`backend/evals/results/latest.md`](../backend/evals/results/latest.md).

## What is measured

| Metric | What it checks | Target |
| --- | --- | --- |
| Extraction recall | Every follow-up in the discharge text becomes an obligation | ≥ 95% |
| Extraction precision | Nothing that wasn't ordered ("No cardiology follow-up is required") becomes one | ≥ 90% |
| Loop state accuracy | At the end of a multi-session case, each follow-up is in the state a careful coordinator expects | ≥ 90% |
| Open loops retained | Follow-ups that are still open are still being tracked after later conversations | 100% |
| No unverified closure | A patient saying "I went" never closes a loop on its own | 100% |
| Contradiction caught | When the patient's date disagrees with the clinic's, the conflict is kept and flagged | 100% |
| Escalation correct | Medication and symptom questions escalate to a clinician; "no chest pain" does not | 100% |
| Date accuracy | Clinic-confirmed dates are remembered weeks later | 100% |

## The dataset

`backend/evals/dataset.py` holds 21 synthetic cases in two groups:

- **Extraction (6):** heart failure, COPD, diabetes, hip replacement, pneumonia
  and atrial fibrillation discharges, including negated orders and a repeat
  bloodwork instruction. Both systems see the same single document.
- **Trajectories (15):** multi-session stories, including the full Michael Chen
  demo arc, self-reported visits, a clinic booking followed by a conflicting or
  agreeing patient date, a patient-reported reschedule and cancellation,
  "not yet" answers, small talk, a caregiver who reports a visit and asks a
  dosing question in one breath, and urgent symptoms.

Expectations are written from the product rules, not from system output. The
first run of the suite found three real defects (a negated order extracted as a
follow-up, a patient's agreeing date downgrading a clinic-confirmed booking, and
a question inside a report overriding the report); all three are fixed.

## How the two systems run

- **Continuum** (`evals/systems.py: run_continuum`) creates a fresh synthetic
  patient and replays every step through the real pipeline: the LangGraph
  agent, the loop engine and MongoDB memory. Clinic bookings go through the same
  `confirm_appointment` path the console uses.
- **Stateless baseline** (`run_baseline`) runs the same `extract()` on the last
  conversation only and takes it at face value: "I saw my doctor" means done,
  the date the patient says is the date.

With an LLM key in `.env`, both systems use that model; without one, both use
the deterministic extractor. Either way the comparison isolates memory and
rules, not model quality. The suite always uses an in-memory database, so it
never touches Atlas data.

## LangSmith

`--langsmith` needs `LANGCHAIN_API_KEY` (or `LANGSMITH_API_KEY`). It creates or
refreshes the `continuum-care-loops` dataset and runs two experiments,
`continuum-…` and `stateless_baseline-…`, scored per metric. Open the dataset in
LangSmith and select both experiments to compare them side by side.

## Regression test

`backend/tests/test_evals.py` runs the whole suite in `pytest` and fails if
Continuum drops below any target or if the baseline stops trailing it on the
memory metrics.
