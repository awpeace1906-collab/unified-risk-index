# Unified Risk Index (URI) v2: streaming

Decompensation prediction for adult emergency department and critical-care
patients, built on the risk factors defined in URI v1. URI v1 is a one-time
triage score. **v2 recalculates every time the EMR sends a value**, scores
how the patient is changing, captures the study's composite outcome from the
same stream, and can be calibrated to real 6–48 h probabilities.

* **Index:** NEWS2 (physiology) + SOFA (organ dysfunction) + CRS2 (biomarkers,
  Charlson comorbidity, medications, frailty). One channel per kind of
  information, so no vital sign is counted twice.
* **Trajectory layer:** NEWS2/SOFA rises, shock index, Hgb drop, lactate
  clearance, KDIGO AKI, troponin delta, oxygenation, ROX.
* **Full v1 coverage:** all 60 history items, 28 medication classes and 33 numeric labs
  are detected from the EMR. Items with no diagnosis code are derived from
  Procedure, Encounter, medication and Consent data, plus the Padua score.
* **Critical care:** respiratory-support ladder, vasopressor count, 21
  correctable causes, and the v1 critical-value panel.
* **Outcome capture:** ICU transfer, intubation, vasopressor start, death and
  RRT, taken from the stream and kept out of the predictors.
* **Escalation floor:** the URI is never less urgent than the NEWS2 / Sepsis-3
  tier the patient already meets.
* **Calibration:** `python -m uri.calibrate` fits per-horizon probabilities
  and reports optimism-corrected AUROC against NEWS2 alone and against URI v1.
* `uri_v1` (the original formula) is reported in every snapshot for comparison.

> Research software for shadow-mode validation. It is not a medical device
> and must not drive clinical decisions until validated (see the URI research
> proposal, Phases 1–4).

## Quick start

The engine needs no dependencies; Python ≥ 3.10. Calibration needs numpy.

```bash
python -m uri demo                       # sepsis, occult GI bleed, hypercapnic resp. failure, stable
python -m uri simulate > feed.ndjson     # write the synthetic FHIR feed
python -m uri replay feed.ndjson --log snapshots.jsonl --outcomes-out outcomes.csv
python -m uri serve --port 8080 --log shadow.jsonl   # receive FHIR pushes
python -m uri.calibrate --snapshots snapshots.jsonl --outcomes outcomes.csv --bootstrap 200 --out model.json
python -m unittest discover -s tests -t .

# Phase 0 (see docs/PHASE0.md)
python -m uri.audit data/phase0_audit.csv          # event rates + decision rule + sample sizes
python -m uri.samplesize --event-rate 0.06         # Riley criteria, derivation and validation
python -m uri.datadict > docs/DATA_DICTIONARY.md   # Phase 1 EHR extraction spec
```

Demo output (abridged). In the occult GI bleed, the trend lifts the index
while v1 still reads LOW, and the NEWS2 floor takes it to HIGH once vitals
cross RCP's emergency tier:

```
08:00  sim-gi_bleed URI   4.8 (LOW)          static  4.8 +traj  0.0 | v1  5.0
14:00  sim-gi_bleed URI  26.9 (LOW-MODERATE) static 14.9 +traj 12.0 | v1 14.1  signals: news2_rise, shock_index, hgb_drop  causes: GI bleed / hemorrhagic shock
16:00  sim-gi_bleed URI  61.0 (HIGH)         static 25.9 +traj 18.0 | v1 46.1
```

## What streams in

| FHIR resource | Used for |
|---|---|
| `Observation` (LOINC) | vitals (incl. BP panel, O₂ flow/FiO₂, GCS), ~40 labs, unit-converted |
| `Condition` (ICD-10-CM) | Charlson categories (Quan coding) and the ~50 v1 PMHx factors |
| `MedicationRequest` / `Statement` / `Administration` | 19 high-risk medication classes |
| `Patient` | age, language / interpreter need, death |
| `Procedure` | recent surgery, intubation (outcome) |
| `Encounter` | recent hospitalization, ICU transfer (outcome) |
| `Consent` | advance directive |
| local flowsheet codes | O₂ device / ventilation mode, Clinical Frailty Scale, RRT activation, … via `local_codes` |

## Each snapshot contains

`uri` (streaming index), `uri_static` (no trend term) and `uri_v1`, band,
escalation floor and its reason, NEWS2/qSOFA/MEWS/SOFA/Charlson/CRS/CRS2
detail, Padua, trajectory signals, 6–48 h windows (fitted probabilities once
a model is loaded), the ranked correctable causes with the reason each
fired, critical values, active risk factors with their evidence status,
composite outcomes observed so far, study eligibility, stale and missing
data, and alerts.

## Docs

* [docs/PHASE0.md](docs/PHASE0.md): event-rate audit protocol, sample sizes, and revisions the proposal needs
* [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md): the Phase 1 extraction specification (generated)
* [docs/SCORES_AND_DATA.md](docs/SCORES_AND_DATA.md): which scores are in, which to add, and which data sets to bring in
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the index formula, trajectory signals, streaming behaviour, and how this fits the Phase 1–4 research plan
* [docs/RISK_FACTORS.md](docs/RISK_FACTORS.md): the evidence-graded registry and its coverage of the v1 panels
* [docs/EXCEL_RECONCILIATION.md](docs/EXCEL_RECONCILIATION.md): cell-reference bugs in the v1 workbook and the decisions they need

## Layout

```
uri/
  events.py     normalized event model and canonical parameters
  fhir.py       FHIR R4 -> events (LOINC, ICD-10-CM, medication text)
  registry.py   risk factors with detection codes and evidence
  series.py     time-ordered per-parameter series
  scores.py     NEWS2, qSOFA, MEWS, SOFA, Charlson, CRS, CRS2, floor, composite
  trends.py     trajectory signals
  causes.py     correctable-cause rules (21)
  labs.py       v1 critical / flag thresholds
  engine.py     per-patient state, recompute, alerts, sinks
  model.py      feature definition + fitted-model scoring
  calibrate.py  per-horizon logistic fit, bootstrap validation (numpy)
  server.py     HTTP receiver (stdlib)
  simulate.py   synthetic FHIR scenarios
  config.py     every weight / threshold, JSON-overridable
  audit.py      Phase 0 event-rate audit
  samplesize.py Riley sample-size criteria
  datadict.py   generates docs/DATA_DICTIONARY.md
examples/       config.json and the demo FHIR feed
templates/      Phase 0 chart-abstraction sheet
```
