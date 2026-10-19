# Unified Risk Index (URI) v2: streaming

URI v1 is a one-time triage score: NEWS2, qSOFA, MEWS and a Composite Risk
Score, combined into a 0–100 index with 6–48 h risk windows and a
correctable-cause panel. **v2 recalculates the index every time the EMR sends
a new value** and adds a trajectory layer that scores how the patient is
changing (a falling hemoglobin, a lactate that is not clearing, a NEWS2 that
keeps rising), not just where they are now.

> Research software for shadow-mode validation. It is not a medical device
> and must not drive clinical decisions until validated (see the URI research
> proposal, Phases 1–4).

## Quick start

No dependencies; Python ≥ 3.10.

```bash
python -m uri demo                       # three synthetic patients streamed through the engine
python -m uri simulate > feed.ndjson     # write the synthetic FHIR feed
python -m uri replay feed.ndjson --log snapshots.jsonl
python -m uri serve --port 8080 --log shadow.jsonl   # receive FHIR pushes
python -m unittest discover -s tests -t .
```

Demo output (abridged): the septic patient climbs from LOW to CRITICAL. The
postpartum patient (previa and twins) reaches HIGH while the v1 formula alone
still reads MODERATE, because the extra risk comes from the trend in blood
loss, hemoglobin and shock index:

```
08:00  sim-pph   URI   3.2 (LOW)          v1  3.2 +traj  0.0
09:00  sim-pph   URI  22.9 (LOW-MODERATE) v1  8.9 +traj 14.0  signals: news2_rise, shock_index, hgb_drop, qbl_ge_500  causes: Postpartum hemorrhage …
09:30  sim-pph   URI  62.2 (HIGH)         v1 42.2 +traj 20.0  signals: … qbl_ge_1000  [band_escalation] [rapid_rise]
```

## What streams in

| FHIR resource | Used for |
|---|---|
| `Observation` (LOINC) | vitals (incl. BP panel, O₂ flow/FiO₂, GCS), ~40 labs, unit-converted |
| `Condition` (ICD-10-CM) | ~65 risk factors, from the v1 PMHx panel and the obstetric hemorrhage factors |
| `MedicationRequest` / `Statement` / `Administration` | 19 high-risk medication classes |
| `Patient` | age |
| local flowsheet codes | anything else (QBL, CFS, …), mapped via `local_codes` in the config |

## Each snapshot contains

`uri` (streaming index) and `uri_static` (v1 formula, for comparison), band,
component breakdown, NEWS2/qSOFA/MEWS/CRS detail, trajectory signals, 6–48 h
windows, the ranked correctable causes with the reason each fired, the
obstetric hemorrhage panel, active risk factors with their evidence status,
stale and missing data, and alerts (band escalation, rapid rise, new cause).

## Docs

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the index formula, trajectory signals, streaming behaviour, and how this fits the Phase 1–4 research plan
* [docs/RISK_FACTORS.md](docs/RISK_FACTORS.md): the evidence-graded registry (pooled ORs from the PPH meta-analysis, v1 priors, tracked factors)
* [docs/EXCEL_RECONCILIATION.md](docs/EXCEL_RECONCILIATION.md): cell-reference bugs in the v1 workbook and the decisions they need

## Layout

```
uri/
  events.py     normalized event model and canonical parameters
  fhir.py       FHIR R4 -> events (LOINC, ICD-10-CM, medication text)
  registry.py   risk factors with detection codes and evidence
  series.py     time-ordered per-parameter series
  scores.py     NEWS2, qSOFA, MEWS, CRS, URI composite, windows
  trends.py     trajectory signals
  causes.py     correctable-cause rules
  engine.py     per-patient state, recompute, alerts, sinks
  server.py     HTTP receiver (stdlib)
  simulate.py   synthetic FHIR scenarios
  config.py     every weight / threshold, JSON-overridable
examples/       config.json and the demo FHIR feed
```
