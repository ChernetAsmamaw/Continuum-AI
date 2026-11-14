# Continuum evaluation results

Run 2026-11-13 22:46 UTC · 21 cases · extractor: `heuristic (no LLM key)`

Continuum (memory + Unclosed Loop Engine) against a stateless baseline: the same
extractor with no memory and no rules, seeing only the latest conversation.

| Metric | Target | Continuum | Stateless baseline | Cases |
| --- | --- | --- | --- | --- |
| Follow-ups extracted from discharge text (recall) | 95% | **100%** | 100% | 6 |
| Extracted follow-ups that were really ordered (precision) | 90% | **100%** | 100% | 6 |
| Follow-ups in the correct state at the end | 90% | **100%** | 15% | 17 |
| Still-open follow-ups the system still tracks | 100% | **100%** | 2% | 15 |
| Patient self-reports NOT treated as proof | 100% | **100%** | 60% | 5 |
| Seeded date contradictions caught and preserved | 100% | **100%** | 0% | 3 |
| Clinical/medication questions escalated, others not | 100% | **100%** | 100% | 15 |
| Clinic-confirmed dates remembered | 100% | **100%** | 50% | 2 |

## Per case

| Case | Continuum | Baseline | Misses (Continuum) |
| --- | --- | --- | --- |
| `extract_heart_failure` | 100% | 100% | — |
| `extract_copd` | 100% | 100% | — |
| `extract_diabetes` | 100% | 100% | — |
| `extract_postop_negation` | 100% | 100% | — |
| `extract_pneumonia_negation` | 100% | 100% | — |
| `extract_af_repeats` | 100% | 100% | — |
| `michael_full_arc` | 100% | 40% | — |
| `self_report_not_verification` | 100% | 25% | — |
| `bloodwork_self_report` | 100% | 25% | — |
| `conflict_after_confirmation` | 100% | 25% | — |
| `agreement_after_confirmation` | 100% | 56% | — |
| `date_recall_question` | 100% | 25% | — |
| `rescheduled_by_patient` | 100% | 25% | — |
| `negated_completion` | 100% | 50% | — |
| `medication_question` | 100% | 33% | — |
| `urgent_symptom` | 100% | 33% | — |
| `no_false_escalation` | 100% | 33% | — |
| `long_gap_memory` | 100% | 50% | — |
| `open_loops_after_smalltalk` | 100% | 33% | — |
| `patient_reported_cancellation` | 100% | 33% | — |
| `caregiver_report_and_dose` | 100% | 50% | — |
