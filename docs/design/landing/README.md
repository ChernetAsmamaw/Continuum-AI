# Landing page proposal

**Status: suggestion only.** The live page (`frontend/src/pages/Landing.tsx`) is unchanged. This folder holds the proposal.

- Clickable prototype: [`prototype/index.html`](prototype/index.html). Open it in a browser; there's no build step. The dark bar at the top is for review only: it says the page is a proposal and switches System / Light / Dark (also `?theme=dark`). The demo buttons open the running app at `http://localhost:8000/login`.
- Product screenshots used by the prototype: [`screens/`](screens/).
- Before: [`before-desktop.png`](before-desktop.png), [`before-375.png`](before-375.png) (current live page).
- After (the prototype): [`after-desktop-light.png`](after-desktop-light.png), [`after-desktop-dark.png`](after-desktop-dark.png), [`after-375-light.png`](after-375-light.png), [`after-375-dark.png`](after-375-dark.png).

---

## 1. Audit of the current page

What makes it read as a generated template:

1. **Glowing hero.** Dark green radial-gradient hero, centred, with a pulsing dot in the kicker and a soft glow behind the product shot.
2. **Italic serif accent line.** "Care doesn't stop at discharge. *Neither should follow-up.*" A slogan with a twist, set in a second typeface and a second colour.
3. **Mono all-caps eyebrows on every section.** "OPERATIONAL MEMORY FOR TRANSITIONAL CARE", "THE UNCLOSED LOOP", "DESIGNED FOR ACCOUNTABLE USE".
4. **Vague copy.** "Operational memory", "one reliable view of every commitment", "Automation where it helps. Human judgment where it matters." Nothing says what the product reads, what it produces, or when something counts as done.
5. **Floating fake UI cards** over the screenshot ("Conflict detected", "Evidence attached · 9:42 AM").
6. **Browser-chrome frame** with traffic-light dots around the screenshot.
7. **Tech-stack logo strip** styled like a "trusted by" row ("One coordination layer across the tools care teams already use": MongoDB Atlas, LangGraph, ElevenLabs…).
8. **Three-up numbered principle cards** (01 / 02 / 03), all equally loud.
9. **Four-step timeline** with numbered circles about "Michael", whom the visitor has no context for.
10. **Check-icon bullet list** on a dark band.
11. **Everything centred, nothing ranked.** Five full-bleed bands in alternating colours; no section matters more than another.
12. **No evidence for the problem.** It asserts that follow-up gets lost and cites nothing.
13. **Accessibility.** Body copy on the dark hero is about 2:1 contrast; the safety list is grey on near-black. No light/dark handling: it looks the same whatever the system setting.

## 2. Proposed structure

One story, top to bottom. Left-aligned, asymmetric grid: section headings in a narrow left column (4/12), content on the right (7/12). Rules between sections, no coloured bands.

| # | Section | Purpose | Content |
| --- | --- | --- | --- |
| 1 | Header | Get to the demo | Wordmark; "How it works", "Rules", "Sign in" |
| 2 | Hero | Say what it does in one sentence | Headline, one paragraph, one ink button. Right: a live-rendered worklist for one synthetic discharge (HTML, not an image), red only on the overdue row |
| 3 | The problem | Why it matters, with sources | Three numbers as a ruled list, each with a linked citation |
| 4 | One discharge, start to finish | How it works | Four numbered steps; three carry a cropped screenshot of the running console |
| 5 | Four rules built into the backend | What makes it different | Proof before closure, crash-safe jobs, sent ≠ done, new codes go to a person. 2×2 ruled grid, no icons |
| 6 | What it is not | Honest framing | Administrative tool, no diagnosis or treatment; synthetic data; hackathon context |
| 7 | Try it | Path into the demo | One sentence on what the demo shows, the same ink button |
| 8 | Footer | | "Synthetic data only. Not for clinical decisions." |

**Visual language** (from [`../README.md`](../README.md) §v2.5): `--bg/--surface/--sunken/--line/--ink/--ink-2/--muted/--red/--focus` tokens, light and dark. Ink is the only action colour; red appears once, on the overdue row. Atkinson Hyperlegible Next for text, Atkinson Hyperlegible Mono for record IDs, citations and dates. Radius 4–6 px, hairline rules, no shadows, gradients, blobs, emoji or icon bullets. Status glyphs (check, ring, dot) from the console spec.

**Contrast (WCAG 2.1 AA):** all text pairs use the v2 tokens, which are ≥ 4.9:1 on every surface (muted on canvas 5.6 light / 6.9 dark; red on surface 6.6 / 7.6; primary button ≥ 15:1). Focus ring 2 px `--focus`. Skip link to main content.

**Mobile (375 px):** single column, nav collapses to "Sign in", worklist "when" moves under the title, wide screenshots crop to a square showing their left half at a readable size. No horizontal scroll (checked: `scrollWidth` = 375).

## 3. Copy

Run through the `unslop` scanners (banned phrases, structure, silhouette, readability) on the rendered text: **0 hard or soft phrase hits.** The structure scan's soft "staccato" flag and the silhouette "callback" flag come from UI labels (worklist rows, captions) and the repeated demo button, which are genre-natural and were kept. Fixed by hand after review: "Nobody owns them after the patient goes home" (unsupported certainty), "The research has measured what happens next" (preview-then-fulfil scaffolding), "Four rules it doesn't bend" (personified slogan). Flesch–Kincaid grade 10.

**Hero**
> Follow-up tracking after hospital discharge
> # A follow-up stays open until there's proof it happened.
> When a hospital discharges a patient, Continuum reads the hospital's own orders: the cardiology referral, the bloodwork, the culture still in the lab. Each becomes a tracked item. An item closes only when a matching result or visit comes back from the EHR and a coordinator signs it off.
> **[Open the coordinator demo]** Synthetic patients. No account needed: choose "Explore as care coordinator".

Worklist (Michael Chen, 64 · Discharged Nov 4 · St. Mary's 5W · heart failure), from the synthetic demo data in `../README.md` §7:

| Item | State | Source | When |
| --- | --- | --- | --- |
| Cardiology follow-up within 7 days | Clinic visit matched by order ID, signed off | ServiceRequest/sr-88213 → Encounter/enc-5521 | Closed Nov 13 |
| Basic metabolic panel | Result matched by order ID, signed off | ServiceRequest · lab | Closed Nov 8 |
| Blood culture, pending at discharge | No growth at 48 h. Waiting for the final report | DiagnosticReport · preliminary | Result pending |
| Home-care assessment | Referral sent. No visit booked | Discharge summary, no order | **2 days overdue** (red) |
| Medication review | Furosemide 40 → 80 mg, spironolactone added | MedicationRequest × 2 | Open |

Caption: "Example worklist for one synthetic patient. Red marks the only overdue item."

**The problem**
> ## Orders are written before the patient leaves. Results arrive after.
> Lab results and clinic visits come back days or weeks after discharge, often to a clinician who has moved on to other patients. Studies have counted how often they are missed.

- **20–62%** of test results for hospital inpatients were never followed up, across the studies in a 2011 systematic review. *Callen et al., BMJ Quality & Safety, 2011*
- **38%** of Medicare heart-failure patients saw a doctor within 7 days of discharge at the median hospital. Hospitals with more early follow-up had fewer readmissions within 30 days. *Hernandez et al., JAMA, 2010*
- **$320M** in Medicare readmission penalties were expected across 2,273 hospitals in fiscal year 2023. *Healthcare Dive, reporting KFF Health News, Oct 2022*

**One discharge, start to finish**
> Screens below are from the running console with synthetic patients. The hospital side is a FHIR test server loaded with the same kind of record a real EHR returns.

1. **The hospital sends a discharge message.** An HL7 ADT^A03 arrives. Continuum pulls the stay over FHIR: orders, booked appointments, results still pending, new medications and the discharge summary. The job saves after each of its five steps. *(screenshot: ingestion jobs — "One discharge was also flagged for identity review.")*
2. **The hospital's orders become follow-ups.** Each referral, lab order and pending result becomes one item that keeps its order ID. The discharge summary is read only for what the orders missed, such as a home-care assessment, and those items are marked as read from text, to confirm.
3. **Continuum chases what's due.** It schedules check-in calls with the patient and creates tasks for the coordinator. Anything past its due date with no proof goes to the top of the list. *(screenshot: needs attention — "Overdue items, with the latest evidence for each.")*
4. **Proof closes the item.** When a result or visit comes back that references the order ID, Continuum attaches it and queues one approval. The coordinator reads the evidence and approves or rejects it. Both are written to the audit log. *(screenshot: approval — "A final lab report matched to its order, waiting for sign-off.")*

**Four rules built into the backend**
- **No proof, no closure.** A follow-up closes when the EHR sends back a result or visit that points to its order ID, and a coordinator approves it. A patient saying "I went" is recorded as patient-reported. It does not close anything.
- **A crash doesn't redo work.** Each discharge runs as a job with five saved steps. If the process dies after step three, the restart begins at step four. It does not pull the record again or create the follow-ups twice.
- **Sent is not done.** Every call and task is written down before it goes out, then marked sent, then confirmed only when the expected proof arrives: a call transcript or the coordinator's decision. An interrupted send is marked unknown and left for a person. It is never retried automatically.
- **New codes go to a person.** When a hospital starts using an order code Continuum hasn't seen, it looks for similar past orders with Atlas Vector Search, proposes a type, and asks a coordinator to confirm before creating the follow-up.

**What it is not**
> Continuum is an administrative tool. It tracks whether follow-up that clinicians already ordered took place. It does not diagnose, suggest treatment or change orders. Results that need a clinical read are assigned to the attending physician.
>
> Every patient in the demo is synthetic. It was built for the MongoDB Agents in Context hackathon (problem 3, "Finish the job") on MongoDB Atlas, LangGraph, FastAPI and React.

**Try it**
> ## Try it with synthetic patients
> The demo opens the coordinator console with a synthetic caseload, its open follow-ups and the approvals waiting on them. On the sign-in page, choose "Explore as care coordinator".
> **[Open the coordinator demo]**

Footer: "Continuum · Synthetic data only. Not for clinical decisions."

## 4. Sources

- Callen J, Georgiou A, Li J, Westbrook JI. The safety implications of missed test results for hospitalised patients: a systematic review. *BMJ Qual Saf* 2011;20(2):194–199. [PMC3038104](https://pmc.ncbi.nlm.nih.gov/articles/PMC3038104/). Reports 20.04%–61.6% of inpatient test results not followed up (the results section gives 61.9%; the page rounds to "20–62%"). This is inpatient results in general, not only tests pending at discharge, and the copy says so.
- Hernandez AF et al. Relationship between early physician follow-up and 30-day readmission among Medicare beneficiaries hospitalized for heart failure. *JAMA* 2010;303(17):1716–1722. [doi:10.1001/jama.2010.533](https://doi.org/10.1001/jama.2010.533). Median hospital 7-day follow-up rate 38%; higher early follow-up associated with lower 30-day readmission.
- [Healthcare Dive, Oct 2022](https://www.healthcaredive.com/news/hospital-readmissions-fines-lower/635538/), reporting KFF Health News (J. Rau): FY2023 HRRP penalties expected to total $320 million across 2,273 hospitals.

## 5. Screenshots

[`screens/`](screens/) are 2× crops of the running console (`/ingestion`, `/console`, `/approvals`), captured from the synthetic demo after three discharges were pulled from a local FHIR test server (built on `backend/evals/fake_fhir.py` and `app/ingest/scenarios.py`) and results were polled back. They show the console's current green theme; re-capture them once the console moves to v2.

## 6. Implementing it in `Landing.tsx` later

The prototype's `<main>` was rendered from a React version of this page, so the port is mechanical.

1. **Stylesheet.** Move the `<style>` block's page rules (everything after the `.proto-bar` rules) into `frontend/src/landing.css` and `import "../landing.css"` from `Landing.tsx`. All rules are scoped under `.lp`, so nothing reaches the console. Drop the `body` background rules; the `.lp` root sets its own background and `min-height: 100vh`.
2. **Fonts.** The console does not load Atkinson yet. Either add the Google Fonts `<link>` to `frontend/index.html` (preferred once the console adopts v2), or inject it from a `useEffect` in `Landing` so only `/` loads it.
3. **Markup.** Convert `prototype/index.html` `<div class="lp">…</div>` to JSX (`class` → `className`). Keep the worklist rows, numbers and rules as arrays (`WORKLIST`, `NUMBERS`, `RULES`) and map over them. Replace demo `href="http://localhost:8000/login"` with `<Link to="/login">`, and the wordmark with `<Link to="/">`.
4. **Images.** Copy `screens/*.png` to `frontend/src/assets/landing/` and import them so Vite fingerprints them. Keep `width`/`height` attributes and `loading="lazy"`; keep the alt text as written.
5. **Theme.** The CSS follows `prefers-color-scheme` and honours `data-theme="light|dark"` on `<html>` or on `.lp`, matching the console tokens. No toggle on the real page.
6. **Remove the old landing.** Delete the `.landing-v2` / `.yc-*` / `.announce-v2` rules in `frontend/src/styles.css` (lines ~45–50 and the `.landing` block near line 4269) and `frontend/src/assets/console-hero.png` if nothing else imports it. Login (`.auth-*`) rules on the same lines stay.
7. **Check.** `npm run build`; at 1440 and 375 px, light and dark: no horizontal scroll, the demo path `/` → `/login` → "Explore as care coordinator" → `/console` still works.
