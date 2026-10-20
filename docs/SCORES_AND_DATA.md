# Which scores, and which data

## Scores

### In the index now

| Score | Role in v2 | Why |
|---|---|---|
| **NEWS2** | Physiology channel (weight 0.45) and the escalation floor | Strongest evidence for ED deterioration of the bedside scores. Its RCP response tiers are what clinicians already act on. |
| **SOFA** (+ **ΔSOFA**) | Organ-dysfunction channel (0.30); a 48 h rise of ≥ 2 is a trajectory signal and a floor | Captures the lab-based organ failure that NEWS2 cannot see. ΔSOFA ≥ 2 is the Sepsis-3 operational definition of organ dysfunction. Scoring it continuously only became possible once labs stream in. PaO₂/FiO₂ falls back to the S/F surrogate (Rice 2007) when there is no ABG. |
| **Charlson Comorbidity Index** (Quan ICD-10 coding, 2011 weights) | Comorbidity part of CRS2 | A published, validated ICD-10 algorithm, replacing the v1 ad-hoc comorbidity points. It is computed automatically from the problem list. |
| **Clinical Frailty Scale** | Frailty part of CRS2 | Already in v1, with evidence that it adds to NEWS2 in older ED patients (Chung 2025). It needs a structured nursing flowsheet row. |
| **Padua Prediction Score** | Derives the v1 "PE risk (Padua ≥ 4)" factor | The v1 panel named Padua; it is now computed from registry factors instead of hand-checked. Reduced mobility is not in the stream yet. |
| **NEWS2 SpO₂ Scale 2** | Used for confirmed hypercapnic patients | RCP-specified. Without it, COPD patients at target saturation score as emergencies. |
| **Shock index**, **KDIGO AKI**, **ROX index** | Trajectory signals | Shock index: occult hypovolemia. KDIGO: acute kidney injury from creatinine trends. ROX < 3.85: high-flow nasal cannula failure (Roca 2019). |
| qSOFA, MEWS | Still computed; in `uri_v1` and the model features only | They reuse NEWS2's inputs (RR, SBP, mentation, HR, temperature) and discriminate less well, so in a weighted sum they mostly count the same vitals a second and third time. The fitted model can still give them weight if the data says they add information. |

### Worth adding next (ranked)

1. **Epic Deterioration Index / Rothman Index / eCART, if your site has one.**
   These are proprietary continuous scores that may already run every 15 min
   in your EMR. Ingest them as comparators, not inputs. Reviewers will ask
   "why not the Epic DI?" before they ask "why not NEWS2?", and shadow-mode
   data answers both.
2. **Hospital Frailty Risk Score (Gilbert 2018).** Frailty from 109 ICD-10
   codes, with no nursing documentation needed, so it covers every patient
   whose CFS was never charted. The code list should be taken from the
   published supplement rather than retyped.
3. **SOFA cardiovascular from vasopressor doses.** It is currently scored
   from the agent count (1 agent = 3, ≥ 2 = 4). With MAR doses in
   mcg/kg/min it can be scored exactly.
4. **Urine output.** Adds the KDIGO urine-output criterion and the SOFA renal
   urine criterion; it is in I&O flowsheets.
5. **Condition-specific scores, only when the condition is present.**
   PESI/sPESI for known PE, MELD-Na for cirrhosis, CURB-65 for pneumonia.
   They refine risk within a known diagnosis, which a general index cannot.
   They belong in the correctable-cause panel, not the composite.

Not recommended as additional composite inputs: SIRS (superseded by
Sepsis-3), REMS (vitals plus age, redundant with NEWS2 + age), and HEART or
Wells (diagnostic, not deterioration).

## Data sets

### To stream into the engine (from your EMR)

| Data | What it adds | Source |
|---|---|---|
| Vitals, labs, problem list, med list | Already mapped | FHIR Observation / Condition / Medication* |
| **MAR with doses** | Exact SOFA cardiovascular (agent count and vasopressor-start outcome are already captured); time to antibiotics | MedicationAdministration |
| **Intake/output** | Urine output (KDIGO, SOFA, MEWS) | Flowsheets |
| **Prior encounters and labs** | Built: pulled at arrival (`serve --history-url`) or included in replayed exports, giving pre-episode baselines for creatinine, Hgb, platelets, bilirubin and SOFA (`uri/baseline.py`). Still to add: vital-sign baselines. | FHIR search at arrival / bulk export |
| **Nursing assessments** | Head-to-toe pass/fail is the strongest single input of the Rothman Index | Flowsheets |
| **Clinician-concern signals** | Frequency of vitals and STAT/repeat lab orders predicts deterioration independently of the values (Rossetti 2021, CONCERN) | ServiceRequest / audit timestamps |
| **ADT / location** | ICU transfer is already captured from Encounter.location; ED boarding time and planned-vs-unplanned still need ADT event types | ADT feed / Encounter.location |
| **O₂ device / ventilation mode** | Respiratory-support ladder (needs one `local_codes` entry per site) | Flowsheets |
| **Continuous monitor data** | Minute-level HR, SpO₂, RR; enables variability features (Sundrani 2023: AUROC 0.80–0.84 from continuous monitoring) | Device integration (Capsule, Philips, GE) |
| **Code status** | Exclusion criterion; already a registry factor (Z66) | Problem list / orders |

### For derivation and validation (Phase 1–3)

| Data set | Use |
|---|---|
| **Your institution's retrospective EHR (bulk FHIR export)** | Phase 1 derivation. Replaying it through `python -m uri replay` gives every snapshot that shadow mode would have produced, not only triage values. |
| **MIMIC-IV-ED + MIMIC-IV** (PhysioNet, credentialed access) | Public ED cohort (about 400,000 ED stays) with triage, repeated vitals, labs, medications, ICU transfers and mortality. Useful for a public external validation and for developing before your IRB data is ready. |
| **A second site's EHR** | Phase 3 geographic validation. The FHIR adapter means the same code runs unchanged. |
| eICU-CRD | ICU-only, so of limited use for ED decompensation; at most for SOFA-trajectory sub-studies. |
| NHAMCS (CDC) | National ED event rates for the grant narrative; no time series. |

## Precision: what changes with data

All v2 weights, the trajectory weight and the floors are still priors. The
step that makes the index precise is `python -m uri.calibrate`: it fits a
logistic model per horizon from snapshots and observed outcomes. It reports
optimism-corrected AUROC and the calibration slope (Riley targets: optimism ≤
0.02, slope ≥ 0.9), along with the AUROC of NEWS2 alone and of URI v1 at the
same landmarks. Once a model is loaded, the 6–48 h windows are fitted
probabilities instead of URI × multiplier.
