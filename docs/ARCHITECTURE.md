# Architecture: from a one-time score to a continuous index

URI v1 is calculated once, at triage, from hand-entered values. v2 keeps the
v1 formula but recalculates it every time the EMR sends a new value, and adds
a layer that scores how the patient's numbers are changing.

```
 EMR ──► interface engine / FHIR Subscription ──► uri.fhir.FhirAdapter
                                                        │  normalized Events
                                                        ▼
                                                 uri.engine.Engine
                               ┌──────────── PatientState (per patient) ─────────────┐
                               │ Series per parameter (sorted, as-of lookups)        │
                               │ active risk factors · medication classes · context  │
                               │ NEWS2 / MAP / URI histories                         │
                               └─────────────────────────────────────────────────────┘
                                                        │ on every batch
              ┌──────────────┬──────────────┬───────────┴────────┬──────────────────┐
              ▼              ▼              ▼                    ▼                  ▼
       scores (NEWS2,   trends         causes            obstetric panel       alerts
       qSOFA, MEWS,     (trajectory    (16 rules)        (pooled ORs)          (band ↑, rapid
       CRS → URI v1)    signals)                                                rise, new cause)
              └──────────────┴──────────────┴────────────────────┴──────────────────┘
                                                        ▼
                                  snapshot ──► sinks (JSONL shadow log, HTTP API, …)
```

## The index

```
URI_static = 100 × Σ wᵢ · min(1, scoreᵢ / maxᵢ)           # v1 formula, current values
URI        = min(100, URI_static + 20 · min(1, trajectory_points / 10))
```

* `URI_static` is the v1 formula applied to the most recent usable value of
  each parameter. It is reported in every snapshot so Phase 1/2 analyses can
  compare v1 and v2 directly.
* The trajectory term can only add risk. An improving trend never pulls the
  index below what the current physiology scores, because a falling lactate
  in a hypotensive patient should not lower the alarm.
* The 20-point weight and the 10-point saturation are new priors
  (`trajectory_weight`, `trajectory_cap`). Like the v1 weights, they are
  targets for the Phase 1 derivation.

## Trajectory signals (`uri/trends.py`)

| Signal | Rule | Points |
|---|---|---|
| `news2_rise` | NEWS2 ≥ 2 (≥ 4) above its 6 h minimum | 2 (3) |
| `shock_index` | HR/SBP ≥ 1.0 (≥ 0.9 if obstetric); +1 if up ≥ 0.2 over 4 h | 2–3 |
| `map_low_sustained` | MAP < 65 on two readings ≥ 15 min apart | 2 |
| `lactate_rising` / `lactate_not_clearing` | rising and ≥ 2, or < 10% clearance from ≥ 2, in 12 h | 2 / 1 |
| `hgb_drop` | ≥ 2 (≥ 1) g/dL below its 24 h maximum | 3 (1) |
| `aki` | creatinine +0.3 mg/dL in 48 h or ≥ 1.5× its 7 d minimum (KDIGO) | 2 |
| `troponin_rising` | above URL and ≥ 20% rise in 12 h | 2 |
| `oxygenation_decline` | SpO₂ down ≥ 4 in 2 h, new O₂, or FiO₂ +10% in 4 h | 2 |
| `rr_rise` | RR ≥ 6 above its 4 h minimum | 1 |
| `gcs_drop` | GCS ≥ 2 below its 6 h maximum | 2 |
| `qbl_ge_500` / `qbl_ge_1000` | cumulative quantitative blood loss (obstetric only) | 1 / 3 |
| `fibrinogen_low` | fibrinogen < 200 mg/dL (obstetric only) | 2 |

## Streaming behaviour

* **Out-of-order data.** Labs often result after later vitals have been
  charted. Every series is ordered by observation time, and each recompute
  runs at the patient's clock (the latest clinical time seen), so a late
  result goes into the right place in the trend without moving the clock
  back.
* **Ageing.** Values are used for 12 h (vitals) or 48 h (labs) and reported
  as stale after 2 h or 24 h. `Engine.tick(now)` recomputes every patient so
  ageing shows even when no new data arrives. Run it from a timer in
  production.
* **Missing data** scores 0, as in v1, and is listed in
  `data_quality.missing_core` so a low URI with no labs can be told apart
  from a low URI with normal labs.
* **Alerts** (band escalation, a rise of ≥ 10 points in 60 min, a new
  correctable cause) are held until a full vitals set is present.

## Fit with the research plan

* **Shadow mode (Phase 2).** Run `python -m uri serve --log shadow.jsonl`
  behind the interface engine. No output is shown to clinicians, and every
  snapshot is written with a timestamp for linkage to the composite outcome
  at 6/12/24/36/48 h.
* **Retrospective derivation (Phase 1).** Replay a historical FHIR bulk
  export (`python -m uri replay export.ndjson --log derivation.jsonl`). The
  output is the same snapshot stream prospective use would have produced, at
  every point in each encounter, not only at triage.
* **Recalibration.** Every weight, threshold and multiplier is in
  `uri/config.py` and can be overridden from a JSON file, so recalibrated
  coefficients can be deployed without code changes.

## Not yet built

* HL7v2 ORU/ADT adapter (many EMR lab feeds are still v2)
* Outcome capture (ICU transfer, RRT, intubation, vasopressor start, death) as
  labelled events for automatic outcome linkage
* Retraction of results sent `entered-in-error` after they were ingested
* Persistence of patient state across restarts
* Authentication/TLS on the HTTP receiver
