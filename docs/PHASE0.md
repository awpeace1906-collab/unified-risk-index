# Phase 0: event-rate audit and study set-up

The research proposal (section 0.1) makes the composite event rate "the
single most important number in your entire research program". This page is
the working protocol for measuring it, plus the tools that turn it into
Phase 1 and Phase 2 sample sizes.

## 1. Event-rate audit (weeks 1–2)

**Population.** About 200 consecutive adult (≥ 18 y) ED admissions.
Consecutive means every admission in the window, not a convenience sample.
Apply the protocol exclusions and record the reason:

* DNR/DNI without treatment intent
* ICU admission orders at triage
* Immediate transfer to the OR without an ED evaluation period

**Outcome (study protocol).** The first of: unplanned ICU transfer, rapid
response team activation, emergent intubation, vasopressor initiation, or
in-hospital death, timed from triage.

**Abstraction.** Copy `templates/phase0_audit.csv` to `data/` (git-ignored;
the sheet holds PHI and must never be committed). Then:

* Delete the example rows.
* Fill one row per admission: triage time, admitted (Y/N), any exclusion
  reason, and the time of each component that occurred.
* Leave a time blank if the component did not occur.
* `ed_revisit_72h` is only needed if the rate turns out low (see the decision
  rule below).

```bash
python -m uri.audit data/phase0_audit.csv
```

The output gives:

* event counts and rates with Wilson 95% CIs, cumulative (0–6 … 0–48 h) and
  by interval;
* the component breakdown;
* the proposal's decision rule;
* Phase 1/2 sample sizes at the observed rate.

If a retrospective export is already available, the same audit can run on
the engine's captured outcomes instead of manual abstraction:

```bash
python -m uri replay export.ndjson --outcomes-out data/outcomes.csv > /dev/null
python -m uri.audit --engine-outcomes data/outcomes.csv --index data/index.csv   # patient_id,triage_time
```

Running both on the same 200 patients also validates outcome capture: the
engine's outcomes should agree with chart review before Phase 1 relies on
them.

**Decision rule (proposal 1.5 and 5.1).**

* **48 h rate ≥ 3%:** single-center retrospective Phase 1 as planned.
* **48 h rate < 3%:**
  * reframe Phase 1 around the 48 h window, with the composite broadened to
    include ED revisit within 72 h;
  * plan Phase 2 as multicenter;
  * treat the Phase 4 RCT as likely underpowered.

Before your own audit is ready, `python -m uri.mimic run` produces the same
report from MIMIC-IV-ED (see [MIMIC.md](MIMIC.md)). It gives a public,
single-centre estimate of the event rate, not a substitute for your site's.

## 2. Sample size: the proposal's numbers need revising

`python -m uri.samplesize --event-rate <rate>` applies the Riley 2019 Part II
criteria. The calculator reproduces the `pmsampsize` result for Riley's
published worked example (n = 662). At the proposal's example rate of 6%:

| Model | Parameters | R²cs 0.15 (proposal) | R²cs 15% of max (conservative) |
|---|---|---|---|
| URI v1 | 4 | n = 220 (14 events) | n = 638 (39 events) |
| URI v2 | 14 | n = 768 (47 events) | n = 2,233 (134 events) |

Two statements in the proposal do not match the Riley criteria it cites:

1. **Phase 1 (section 1.5)** says 600–1,200 events, i.e. N ≈ 10,000–20,000
   encounters at 6%. Riley's criteria give **hundreds to ~2,200 encounters**
   (tens to ~130 events), depending on the number of candidate parameters and
   the anticipated R². That makes a single-center Phase 1 far more feasible
   than the proposal suggests. The overstatement could also draw a
   methodological challenge from a grant reviewer.
2. **Phase 2 (section 2.5)** says about 500 encounters (30 events) suffice
   for validation. External-validation guidance asks for **at least 100
   events** (Collins 2016). Riley 2021 adds O/E precision and the
   calibration slope, which usually sets the minimum. `uri.samplesize`
   implements the slope criterion. At 6% and C 0.75 it gives a minimum of
   **~8,800 encounters (~530 events)**; at C 0.80, ~6,600. That makes Phase
   2 multicenter. The sequential stopping rule in 2.5 can stay as an interim
   look, but the planned minimum should rise. Recompute with `--lp <mean> <sd>`
   from the Phase 1 model. The evidence is in
   [PI_DECISIONS_EVIDENCE.md](PI_DECISIONS_EVIDENCE.md).

Use the conservative R² until the Phase 1 model gives a real one. Also note
that streaming produces many snapshots per patient, but the effective sample
size is patients, not snapshots. `uri.calibrate` landmarks one snapshot per
patient-hour and bootstraps by patient for this reason.

## 3. Other Phase 0 items (proposal 0.2–0.4)

* **IRB.** QI / exempt pathway for the audit. For Phases 1–2, the shadow-mode
  language from proposal 0.2 applies to the streaming engine as well: it
  writes snapshots to a log (`--log`) and displays nothing to clinicians.
* **Data request.** `docs/DATA_DICTIONARY.md` is the extraction
  specification for the Phase 1 export. It is generated from the code, so it
  always matches what the engine reads.
* **Co-investigator.** Recruit a senior EM faculty co-I before the IRB
  submission (proposal 0.3).
* **Invention disclosure.** File before any public presentation (proposal
  0.4). The disclosure should now also cover the streaming trajectory layer,
  the escalation floor and the outcome-capture design, not just the v1
  composite.
* **v1 workbook decisions.** `docs/EXCEL_RECONCILIATION.md` lists cell
  references that need a PI decision before Phase 1 describes the scoring.
