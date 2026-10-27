# Architecture: from a one-time score to a continuous index

Scope: decompensation prediction in adult emergency department and
critical-care patients, built on the risk factors defined in URI v1.

URI v1 is calculated once, at triage, from hand-entered values. v2
recalculates every time the EMR sends a value, scores how the patient is
changing, captures the study's composite outcome from the same stream, and
can be calibrated to real probabilities from those outcomes.

```
 EMR ──► interface engine / FHIR Subscription ──► uri.fhir.FhirAdapter
                                                        │  normalized Events
                                                        ▼
                                                 uri.engine.Engine
                               ┌──────────── PatientState (per patient) ─────────────┐
                               │ Series per parameter (sorted, as-of lookups)        │
                               │ active risk factors · medication classes · context  │
                               │ NEWS2 / SOFA component histories · MAP · URI        │
                               │ resp. support level · vasopressor count · outcomes  │
                               └─────────────────────────────────────────────────────┘
                                                        │ on every batch
        ┌───────────────┬───────────────┬───────────────┼───────────────┬──────────────┐
        ▼               ▼               ▼               ▼               ▼              ▼
   NEWS2, SOFA,     trajectory      escalation      risk windows    correctable    alerts
   CRS2 (+v1        signals         floor           (fitted model   causes
   scores)                                          or v1 mult.)
        └───────────────┴───────────────┴───────────────┴───────────────┴──────────────┘
                                                        ▼
                                  snapshot ──► sinks (JSONL shadow log, HTTP API, …)
                                                        │
     outcomes (captured from ADT / MAR / Procedure) ──► uri.calibrate ──► model.json ──► Engine(model_path)
```

## The index

```
uri_static = 100 × [0.45·min(1, NEWS2/20) + 0.30·min(1, SOFA/15) + 0.25·min(1, CRS2/30)]
uri        = max( floor, min(100, uri_static + 20 · min(1, trajectory_points / 10)) )
uri_v1     = the v1 formula (NEWS2/qSOFA/MEWS/CRS, 40/25/20/15), reported for comparison
```

**One channel per kind of information.** v1 sums NEWS2, qSOFA and MEWS, which
read the same RR, SBP and mentation, and then adds NEWS2 × 0.22 and qSOFA × 4
again inside the CRS, so a respiratory rate of 25 counts four times. v2 uses:

* **NEWS2** for bedside physiology
* **SOFA** for organ dysfunction (P/F or S/F, platelets, bilirubin, MAP or
  vasopressors, GCS, creatinine)
* **CRS2** for what neither of those covers: lactate/pH/HCO₃,
  electrolytes/glucose, Hgb/INR, troponin/BNP, procalcitonin/WBC, the
  Charlson index, high-risk medications, and age/frailty

**Escalation floor.** A weighted average dilutes a single alarming channel:
NEWS2 7 with normal labs is about 16 by weight alone. The floor keeps the URI
at least as urgent as the published tier that channel already triggers:

| Condition | Floor |
|---|---|
| NEWS2 ≥ 7 (RCP high) | 61 (HIGH) |
| NEWS2 5–6 (RCP medium) | 41 (MODERATE) |
| NEWS2 single parameter = 3 | 21 |
| ΔSOFA ≥ 2 in 48 h (Sepsis-3) | 41 |
| Vasopressor + lactate > 2 (Sepsis-3 septic shock criteria) | 81 (CRITICAL) |

**Trajectory** can only add risk. An improving trend never lowers the index
below what current physiology scores.

All weights, maxima, floors and the trajectory weight are priors.
`uri.calibrate` replaces them for the risk windows with fitted coefficients.

**NEWS2 SpO₂ scale.** Scale 2 (target 88–92%) is used when hypercapnic
respiratory failure is charted, or inferred from COPD/OSA plus a blood gas
with pCO₂ > 45 (RCP 2017). Otherwise Scale 1. Without this, every COPD
patient sitting at their target saturation scores as a NEWS2 emergency.

## Critical-care inputs

| Input | Source | Used in |
|---|---|---|
| Respiratory support level 0–4 (room air, low-flow, HFNC, NIV, invasive) | O₂ device / ventilation-mode flowsheet row (map with `local_codes` → `resp_support`; text is classified) | SOFA (HFNC/NIV/vent count as respiratory support; flow-based FiO₂ estimates are not used on HFNC/NIV), escalation signal, intubation outcome |
| Concurrent vasopressor/inotrope agents | Active medication administrations (word-start match, so norepinephrine ≠ epinephrine) | SOFA cardiovascular (1 agent = 3, ≥ 2 = 4), escalation signal, septic-shock floor |
| FiO₂, P/F or S/F, ROX | Flowsheets, ABG | SOFA respiration, ROX signal |

## Trajectory signals (`uri/trends.py`)

Score rises (NEWS2, SOFA) are computed **per component, against that
component's own earlier values**. A lab resulting for the first time is new
information, not deterioration, so a first creatinine of 3.8 raises SOFA but
does not fire `sofa_rise`.

| Signal | Rule | Points |
|---|---|---|
| `news2_rise` | NEWS2 ≥ 2 (≥ 4) above its 6 h minimum | 2 (3) |
| `sofa_rise` | SOFA ≥ 2 above its 48 h minimum (Sepsis-3) | 3 |
| `shock_index` | HR/SBP ≥ 1.0; +1 if up ≥ 0.2 over 4 h | 2–3 |
| `map_low_sustained` | MAP < 65 on two readings ≥ 15 min apart | 2 |
| `lactate_rising` / `lactate_not_clearing` | rising and ≥ 2, or < 10% clearance from ≥ 2, in 12 h | 2 / 1 |
| `hgb_drop` | ≥ 2 (≥ 1) g/dL below its 36 h maximum (spans q24h draws), or ≥ 2 (≥ 3) below the pre-episode baseline | 3 (1); 2 (3) |
| `aki` | creatinine +0.3 mg/dL in 48 h or ≥ 1.5× its 7 d minimum (KDIGO) | 2 |
| `troponin_rising` | above URL and ≥ 20% rise in 12 h | 2 |
| `oxygenation_decline` | SpO₂ down ≥ 4 in 2 h, new O₂, or FiO₂ +10% in 4 h | 2 |
| `rox_low` | ROX < 3.85 with a charted FiO₂ (HFNC/NIV) | 2 |
| `rr_rise` | RR ≥ 6 above its 4 h minimum | 1 |
| `gcs_drop` | GCS ≥ 2 below its 6 h maximum | 2 |
| `resp_support_escalation` | step up the support ladder in 6 h; ≥ 2 steps or to NIV/invasive = 3 | 2–3 |
| `pressor_escalation` | ≥ 2 concurrent agents, with one added within 6 h | 3 |

## Correctable causes (`uri/causes.py`)

The 15 v1 causes, plus six driven by v1 laboratory items that previously fed
nothing:

| # | Cause | Trigger (examples) |
|---|---|---|
| 16 | Hypercapnic respiratory failure | pCO₂ > 50 with pH < 7.35, or ETCO₂ > 50 |
| 17 | Hepatic encephalopathy / acute liver failure | ammonia > 100; cirrhosis + altered mentation + ammonia > 50; bilirubin > 5 + INR > 1.5 + encephalopathy |
| 18 | Thyroid storm / myxedema coma | TSH < 0.1 with HR > 130 / fever / altered mentation; TSH > 10 with hypothermia / bradycardia / altered mentation |
| 19 | Severe hypomagnesemia / hypophosphatemia | Mg < 1.2 mg/dL; phosphorus < 1.5 |
| 20 | Acute pancreatitis | lipase > 3× ULN (`lipase_uln_u_l`) |
| 21 | Coagulopathy with bleeding risk | INR > 3, fibrinogen < 150 or platelets < 50, with bleeding or anticoagulation |

Two precision fixes to existing rules: **sepsis** needs an infection marker
(qSOFA alone also fires in hemorrhage, PE and heart failure). **Metabolic
acidosis** excludes low pH with raised pCO₂, which is respiratory acidosis.
Adrenal crisis now also fires on random cortisol < 15 with hypotension.

## Critical values (`uri/labs.py`)

Every "Critical / Flag Threshold" from the v1 Patient Input sheet is evaluated
on each recompute and listed in `critical_values`, giving the most severe
breach per analyte and direction. D-dimer uses the age-adjusted cut-off. The
count of critical values is a model feature.

## Outcome capture (the study's composite endpoint)

| Outcome | Captured from |
|---|---|
| Unplanned ICU transfer | Encounter.location whose name matches `icu_location_patterns` |
| Emergent intubation | Procedure SNOMED 112798008 / CPT 31500, or respiratory support stepping up to invasive |
| Vasopressor initiation | First MedicationAdministration of norepinephrine, epinephrine, vasopressin, phenylephrine, dopamine or angiotensin II |
| In-hospital death | Patient.deceasedDateTime |
| RRT activation | Site-specific: map the RRT flowsheet row or procedure code to `outcome:rrt` in `local_codes` |

Outcomes are **labels, never predictors**: they do not move the clock or
change any score, and a snapshot only shows outcomes at or before its own
time. Events more than 1 h before the first measurement are prior history,
not outcomes. `python -m uri replay … --outcomes-out outcomes.csv` writes them
in the format `uri.calibrate` reads, so a historical export yields both the
predictors and the labels in one pass. "Unplanned" ICU transfer cannot be
told apart from planned admission from location data alone; that needs
chart review or an order-based rule per site.

**Study eligibility.** Each snapshot flags protocol exclusions that can be
read from the stream: age < 18, and DNR/DNI on file (needs review for
treatment intent). ICU orders at triage and immediate OR transfer need
ADT/order data and are not checked yet.

## Calibration (`uri/calibrate.py`, `uri/model.py`)

```
python -m uri replay export.ndjson --log snapshots.jsonl     # or shadow-mode log
python -m uri.calibrate --snapshots snapshots.jsonl --outcomes outcomes.csv \
    --encounters encounters.csv --bootstrap 200 --out model.json
# then set "model_path": "model.json" in the config
```

* Landmarking: one snapshot per patient per hour, only before the first
  composite event, so post-event data cannot leak into the training rows.
* One L2-penalized logistic model per cumulative horizon (6/12/24/36/48 h),
  on the features in `uri/model.py`: all component scores, ΔSOFA, trajectory
  points, age, how much of SOFA was measured, critical-value count,
  respiratory-support level and vasopressor count.
* The report gives apparent and patient-level bootstrap optimism-corrected
  AUROC, calibration slope and Brier score. Its comparators are NEWS2 alone,
  URI v1 and uncalibrated URI v2 at the same landmarks.
* Encounter end times censor rows whose follow-up ends before the horizon.

## Arrival-time history and baselines (`uri/history.py`, `uri/baseline.py`)

A live feed only carries what happens from now on. With
`python -m uri serve --history-url <FHIR base>`, the first time a patient
appears their prior results are fetched from the EHR's FHIR API. The bearer
token is read from `URI_FHIR_TOKEN`, never from the command line. The pull
fetches:

* 12 months of creatinine, hemoglobin, platelets, bilirubin and pCO₂;
* labs and vitals from the last 48 h;
* the problem list and active medications;
* encounters (90 d) and procedures (30 d);
* demographics and advance directives.

History is preloaded without emitting snapshots. The pull is best effort: a
failed query is recorded and skipped, and scoring never waits on it. Replaying
an export that already contains prior results gives the same result without
any pull.

**Visits (episodes).**

* **Primary: the EHR's encounter record.** ED, observation and inpatient
  encounters (FHIR `Encounter`, from ADT) are merged into one visit when they
  follow each other within 1 h (ED discharge → admission). The visit starts
  at arrival (minus 1 h for triage charted before registration) and closes
  at discharge. A long pause in charting mid-stay does not split it, and
  clinic results drawn before arrival are baseline.
* **Fallback, with no encounter data:** the visit starts after the last gap
  of more than 36 h (`episode_gap_hours`) in the patient's results as a
  whole, not per test. A lab not repeated for 72 h does not split it while
  other results keep arriving, and q24h ICU labs alone never split it. The
  36 h is a judgement call that Phase 1 should replace with the empirical
  within-encounter gap distribution.

Each snapshot reports `visit.start`, `visit.open` and `visit.determined_by`.
Results before the visit form the baseline. Outcomes count
only inside the current episode, so a prior admission's ICU stay pulled in
with the history is not an outcome. Calibration rows come from each
patient's latest episode.

**Baselines** (looking back 365 days from the episode start):

| Item | Baseline |
|---|---|
| Creatinine | Lowest value in the 7 days before the episode, else the median of days 8–365 |
| Hemoglobin, platelets, bilirubin | Median of prior values |
| SOFA | Coagulation, liver and renal points of those baselines; respiratory, cardiovascular and CNS taken as 0 |

**What uses them:**

* **SOFA in the composite** is the acute change above baseline, per organ
  (Sepsis-3). A CKD patient at their usual creatinine is not scored as new
  renal failure; chronic burden is carried by Charlson. With no prior results
  the baseline is assumed 0, the Sepsis-3 convention. The snapshot reports
  total, baseline and acute SOFA.
* **`sofa_rise`** also fires when acute SOFA ≥ 2 above a known baseline,
  which catches organ dysfunction already present at arrival. The escalation
  floor uses the same rule.
* **`aki`** is staged per KDIGO against the baseline from a single arrival
  value: ≥ 1.5× is stage 1, ≥ 2× stage 2, ≥ 3× or ≥ 4.0 mg/dL stage 3.
  Without a baseline it falls back to the 7-day minimum.
* **`hgb_drop`** also fires when Hgb is ≥ 2 g/dL below the patient's usual
  value, catching bleeding already under way at arrival, while chronic anemia
  at its usual level does not fire.
* **Model feature** `sofa_acute`.

## Streaming behaviour

* **Replay order.** `python -m uri replay` sorts the whole export by
  clinical time before feeding it (FHIR bulk exports are grouped by resource
  type); `--as-is` keeps file order.
* **Out-of-order data.** Series are ordered by observation time, and the
  clock never moves back, so late labs land in the right place in the trend.
* **Ageing.** Vitals are used for 12 h. A lab's latest value is used for
  the whole open visit, however long ago it was drawn, but never from a
  previous visit (`labs_hold_through_episode`). Once the visit closes
  (discharge, or results stopping for more than 36 h without encounter
  data), labs age out after 48 h. `Engine.tick(now)` recomputes without new
  data.
* **Consciousness and oxygen status** (GCS / AVPU; O₂ flow, FiO₂, device)
  are often charted once and not repeated. They carry forward like labs:
  the last value documented in this visit is used until a new one is
  charted, never one from a previous visit (`carry_forward_through_visit`).
  Older than 12 h, NEWS2 flags it as carried forward and it is reported
  stale. Never documented in the visit, it is missing, not assumed normal:
  NEWS2 then reports `upper`, its value if every missing item were maximal.
* **Staleness** is decided per result (`uri/staleness.py`,
  [STALENESS_RULES.md](STALENESS_RULES.md)):
  * vitals follow the NEWS2 minimum monitoring frequency (12 h at NEWS2 0
    down to 30 min at ≥ 7);
  * lactate is 4 h when > 2 mmol/L;
  * troponin 6 h; blood gases and glucose 6 h; electrolytes 12 h;
  * SOFA labs 24 h; Hgb 24 h, or 6 h while falling; BNP and procalcitonin 48 h.

  **All of these windows are placeholders pending PubMed confirmation.**
  Each stale entry carries its window, the reason and `rule_status`.
  "Hours since measured" for vitals, lactate, Hgb and creatinine are model
  features, so Phase 1 calibration can test the windows against outcomes.
* **Missing data** scores 0 and is listed in `data_quality.missing_core`.
  The model also sees how much of SOFA was measured.
* **Alerts** (band escalation, a rise of ≥ 10 in 60 min, a new correctable
  cause) are held until a full vitals set is present.

## Not yet built

* HL7v2 ORU/ADT adapter
* Planned vs unplanned ICU transfer; ICU orders at triage / immediate OR transfer exclusions
* Baselines for vitals (e.g. usual blood pressure in chronic hypertension) and for respiratory / CNS SOFA
* SMART backend-services token acquisition for the history pull (site-specific)
* Vasopressor doses (mcg/kg/min) for exact SOFA cardiovascular; urine output for KDIGO/SOFA
* Infusion stop times: a completed MedicationAdministration currently stays "active"
* Retraction of results sent `entered-in-error` after ingestion
* Persistence across restarts; authentication/TLS on the receiver
