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
| `hgb_drop` | ≥ 2 (≥ 1) g/dL below its 24 h maximum | 3 (1) |
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

## Streaming behaviour

* **Out-of-order data.** Series are ordered by observation time, and the
  clock never moves back, so late labs land in the right place in the trend.
* **Ageing.** Values are used for 12 h (vitals) or 48 h (labs) and reported
  as stale after 2 h or 24 h. `Engine.tick(now)` recomputes without new data.
* **Missing data** scores 0 and is listed in `data_quality.missing_core`.
  The model also sees how much of SOFA was measured.
* **Alerts** (band escalation, a rise of ≥ 10 in 60 min, a new correctable
  cause) are held until a full vitals set is present.

## Not yet built

* HL7v2 ORU/ADT adapter
* Planned vs unplanned ICU transfer; ICU orders at triage / immediate OR transfer exclusions
* Prior-encounter baselines (creatinine, Hgb, SOFA) fetched at arrival
* Vasopressor doses (mcg/kg/min) for exact SOFA cardiovascular; urine output for KDIGO/SOFA
* Infusion stop times: a completed MedicationAdministration currently stays "active"
* Retraction of results sent `entered-in-error` after ingestion
* Persistence across restarts; authentication/TLS on the receiver
