# MIMIC-IV-ED: retrospective test run

Your site's IRB data will take time to arrive. Meanwhile,
`uri.mimic` runs the whole pipeline on MIMIC-IV-ED, about 400,000
de-identified ED stays from Beth Israel Deaconess, 2011–2019:

* Phase 0 event rates;
* a replay through the streaming engine;
* a first calibration against NEWS2 and URI v1.

## 1. Get access (you, not the code)

1. Create a [PhysioNet](https://physionet.org) account.
2. Complete the CITI course *Data or Specimens Only Research* and upload the
   certificate.
3. Request credentialed access, then sign the data use agreements for
   **MIMIC-IV** and **MIMIC-IV-ED**. Approval usually takes days to a couple
   of weeks.
4. Download both (`hosp/`, `icu/` from MIMIC-IV; `ed/` from MIMIC-IV-ED).
   The `.csv.gz` files can stay compressed.
5. Optional: the CMS ICD-9 → ICD-10 General Equivalence Mapping
   (`2018_I9gem.txt`, public on cms.gov). It lets diagnoses from before
   October 2015 count as comorbidities. Without it, those diagnoses are
   skipped, and `extract_counts.json` reports how many.

**Read the data use agreement before choosing where to run this.** PhysioNet
restricts sending credentialed data to third-party online services, which
includes AI assistants. Run the pipeline on your own machine or another
environment the agreement allows, not in a cloud AI session.

## 2. Run

```bash
# dry run on synthetic tables in the MIMIC layout (no access needed)
python -m uri.mimic demo-data data/mimic-demo
python -m uri.mimic run --mimic-dir data/mimic-demo/mimiciv --ed-dir data/mimic-demo/mimic-iv-ed \
    --out data/mimic-demo/out --bootstrap 0

# the real thing: start with a sample, then the full cohort
python -m uri.mimic run --mimic-dir ~/physionet/mimiciv/3.1 --ed-dir ~/physionet/mimic-iv-ed/2.2 \
    --out data/mimic --limit 20000 --icd9-gem ~/cms/2018_I9gem.txt --config examples/mimic_config.json
```

Most of the time goes into reading `labevents` (about 120–160 million rows),
whatever `--limit` is. Expect tens of minutes on a laptop. Calibration loads
every landmark snapshot into memory, so keep `--limit` to tens of thousands of
stays for the first runs. Calibration needs numpy; without it the run stops
after the replay.

## 3. Outputs (`--out`, under the git-ignored `data/`)

| File | What it is |
|---|---|
| `audit.csv` + printed report | Phase 0: composite rates at 6–48 h with Wilson CIs, components, the decision rule, sample sizes at the observed rate |
| `events.sqlite` | Every extracted event; history rows flagged |
| `extract_counts.json` | How many vitals, labs, diagnoses and medications mapped, and how many ICD-9 codes did not |
| `snapshots.jsonl` | One compact engine snapshot per stay-hour, 0–48 h |
| `outcomes.csv`, `encounters.csv` | Composite outcomes as the engine observed them; follow-up ends for censoring |
| `model.json` + printed report | Landmark model per horizon; AUROC (optimism-corrected with `--bootstrap`) vs NEWS2 and URI v1 |

## 4. How the tables map

| URI input | MIMIC source | Notes |
|---|---|---|
| Patient (one per ED stay) | `ed/edstays` | id `subject_id-stay_id`; encounter = ED arrival to hospital discharge (or ED departure if not admitted) |
| Vitals | `ed/triage` (at arrival), `ed/vitalsign` | °F converted; values outside physiological limits dropped |
| Labs | `hosp/labevents` via `d_labitems` label and fluid | 32 blood parameters; urine ketones and WBC, CSF WBC, pleural pH as their own parameters; units converted; values outside physiological limits dropped and counted. Non-blood labels that did not map are listed in `extract_counts.json` |
| Baselines | `labevents` in the 365 days before arrival | creatinine, Hgb, platelets, bilirubin, pCO2 |
| Comorbidities | `hosp/diagnoses_icd` of admissions discharged **before** arrival | ICD-10 direct; ICD-9 via GEM |
| Medications | `ed/medrecon` (home meds), `ed/pyxis` (ED doses) | same ingredient classes as the FHIR adapter |
| Age, sex, prior discharge | `hosp/patients`, `hosp/admissions` | age = anchor_age + years since anchor_year |
| ICU transfer | `hosp/transfers` care unit | MICU, SICU, CVICU, CCU, Neuro SICU, TSICU |
| Intubation | `icu/procedureevents` 224385 | |
| Vasopressor start | `icu/inputevents`; `ed/pyxis`; `hosp/emar` | norepinephrine, epinephrine, vasopressin, dopamine, phenylephrine; products that act locally (lidocaine or bupivacaine with epinephrine, ophthalmic or nasal phenylephrine, racemic epinephrine) excluded |
| Death | `hosp/admissions.deathtime`; ED disposition *expired* | |
| ED revisit 72 h | next `edstays` arrival within 72 h of departure | |

## 5. Limitations to state in any write-up

* **No ward vital signs.** MIMIC-IV records vitals in the ED and ICU only.
  After a ward admission, the engine sees labs but no vitals until ICU
  transfer, so trajectory signals are weaker there than they would be with
  your EHR's flowsheets. The result is conservative for the trend layer.
* **No GCS, O₂ device or FiO₂ in the ED tables.** The engine does not
  assume them normal. NEWS2 lists them as missing, reports the range the
  true score lies in (`total` to `upper`), and the model gets the count of
  undocumented NEWS2 parameters as a feature. SOFA's CNS component is
  missing. MIMIC NEWS2 totals are therefore lower bounds.
* **IM epinephrine for anaphylaxis** in the ED counts as a vasopressor start;
  `pyxis` has no route. Add `hosp/emar_detail` route filtering if
  anaphylaxis turns out to be common in the cohort.
* **No rapid-response record.** RRT is absent from the composite.
* **ED intubations** are coded with a date only. They are not used as
  outcomes, but those patients go to an ICU, so the ICU transfer captures
  them.
* **Troponin T in MIMIC is the conventional assay**, not high-sensitivity.
  Values are converted to ng/L. Set `troponin_url_ng_l` to 10 (Roche 4th
  generation 99th percentile) in the config (`examples/mimic_config.json`).
* **Direct ICU admissions from the ED.** Some patients go from the ED
  straight to an ICU: shock, intubation in the ED, STEMI to the CCU.
  MIMIC records this as an ICU transfer at ED departure. There are two
  ways to treat it:
  * **Count it (default).** It is a real decompensation, and the index
    streams during the ED stay, where it could have flagged the patient.
    But many of these patients are critical at triage. They are easy
    cases that raise the event rate and flatter the AUROC.
  * **Exclude it (`--exclude-direct-icu`).** The cohort becomes patients
    admitted to a ward or step-down unit, and the outcome is deterioration
    *after* that decision. This matches the proposal's "unplanned ICU
    transfer" and Delgado 2013's ED-admission cohort.

  Report the excluded version as the primary analysis and the counted
  version as a sensitivity analysis.
* **Single centre, academic, 2011–2019, dates shifted per patient.**
  External-validation conclusions need a second site (your Phase 2).
