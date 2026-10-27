# Evidence for the open PI decisions

Two sets of decisions were open: (a) the v1 workbook's comorbidity and
medication terms ([EXCEL_RECONCILIATION.md](EXCEL_RECONCILIATION.md) rows 7–8)
and (b) the proposal's sample-size statements ([PHASE0.md](PHASE0.md) §2).
This page sets out what the literature says about each, with a
recommendation. Sources were retrieved from PubMed / PubMed Central on
2026-10-26. "Full text" means the passage was read in PMC. "Abstract" means
only the abstract was available, so the claim rests on the abstract.

## (a) v1 comorbidity term: CKD or type 1 diabetes

The workbook's comorbidity formula reads the DM type 1 row, but the
manuscript lists CKD.

**No study compares the two head to head as predictors of short-term
decompensation in ED or critical-care patients.** The evidence is indirect
and points both ways:

| Evidence | Favours | Source (access) |
|---|---|---|
| Original Charlson weights: moderate/severe renal disease 2; diabetes 1, or 2 with end-organ damage. Type 1 and type 2 are not distinguished. Weights fitted to 1-year mortality in 559 medical patients. | CKD, mildly | Charlson 1987 (abstract; weights from the paper's table, not checked in full text) |
| Updated Charlson weights: renal disease 1; diabetes without complication 0, with chronic complication 1. Fitted to 1-year mortality after discharge, validated on in-hospital mortality in 6 countries (C 0.73–0.88). Diabetes type not distinguished. | CKD | Quan 2011 (abstract; individual weights from the paper's table, not in PMC) |
| 178,315 ED ward admissions, 2.4% unplanned ICU transfer within 24 h: predictors were acute presentations (sepsis OR 2.5, pneumonia 1.5, MI 1.5, COPD 1.4) plus aggregate comorbidity burden; neither CKD nor type 1 DM singled out. | Neither | Delgado 2013 (abstract) |
| Infection-related hospitalization vs matched controls: type 1 DM IRR 3.71, type 2 1.88; death from infection, type 1 vs type 2, IRR 2.19. | Type 1 DM | Carey 2018 (abstract) |
| Hospitalized with COVID-19 or influenza, type 1 vs type 2: 28-day death HR 1.77 and 1.80 (age/sex adjusted), still significant after comorbidity adjustment. | Type 1 DM | Swedish registry 2022 (abstract) |

**What follows from it:**

* Charlson, in both versions, weights renal disease at least as high as
  uncomplicated diabetes, but it never separates type 1 from type 2, so it
  cannot settle this question.
* Type 1 diabetes carries real risk, chiefly severe infection and infection
  death. The engine captures its DKA and hypoglycaemia pathway (glucose,
  bicarbonate, anion gap, insulin), but not this infection risk until the
  patient is already septic.
* The strongest reason for CKD in v1 is fidelity: the manuscript lists CKD,
  and the workbook formula reading the DM1 row looks like one of the
  cell-reference errors in [EXCEL_RECONCILIATION.md](EXCEL_RECONCILIATION.md).

**Recommendation:**

1. **v1:** CKD (1 point), as the engine already does, so `uri_v1`
   reproduces the published manuscript.
2. **Phase 1:** both are candidate predictors. `uri.model.FEATURES` carries
   `ckd` and `dm1` as binary features, so the derivation decides whether
   either earns weight beyond Charlson, SOFA and the trajectory signals.

The v2 composite uses the full Charlson index (renal disease and both
diabetes categories) and is unaffected by the v1 choice.

## (a) v1 medication weights

The workbook's medication formula reads the wrong cells. Its label is
"Anticoag, diuretics, insulin, opioids", and the engine reconstructs it as
anticoagulant 1, insulin 1.5, opioid 1.

The best evidence on which drug classes send adults to hospital is US
national ADE surveillance (NEISS-CADES):

| Source | Population | Finding |
|---|---|---|
| Budnitz 2011, abstract | Emergency hospitalizations for ADEs, age ≥ 65 | warfarin 33.3%, insulins 13.9%, oral antiplatelets 13.3%, oral hypoglycemics 10.7%. These four account for 67%. |
| Shehab 2016, full text | ED visits for ADEs, all ages | anticoagulants 17.6%, antibiotics 16.1%, diabetes agents 13.3%, opioid analgesics 6.8%, antiplatelets 6.6%, RAS inhibitors 3.5%, antineoplastics 3.0%, sedatives/hypnotics 3.0% |

**Recommendation:**

1. **Weight anticoagulants at least as high as insulin.** They are the
   leading class in both studies. Suggested: anticoagulant 1.5, insulin 1.5.
2. **Keep opioids at 1.** The share is smaller, but opioids also feed the
   hypercapnia and sedation pathways the engine scores separately.
3. **Leave diuretics unweighted in v1.** Neither study names them among the
   leading classes. Their risk shows up through creatinine, potassium and
   sodium, which the engine already scores. Keep the high-dose loop-diuretic
   flag.
4. **Consider antiplatelets and sulfonylureas for v2's CRS2.** Both are
   among the top four classes for older adults. The engine already detects
   them, but they carry no points yet.

These are priors. The ADE studies measure how often each class causes an ADE
visit across the population, not the per-patient risk of decompensating once
admitted. Phase 1 re-derives every weight. Weights can be changed in
`examples/config.json` / registry `crs_points` without code changes.

## (b) Sample size

### Phase 1 derivation: the proposal overstates it

The proposal's 600–1,200 events (10,000–20,000 encounters at 6%) comes from
events-per-variable rules of thumb. Riley 2019 Part II (full text) replaces
these with three criteria:

* expected shrinkage ≥ 0.9;
* apparent vs adjusted Nagelkerke R² within 0.05;
* overall risk estimated to ± 0.05.

`uri.samplesize` reproduces the `pmsampsize` worked example (n = 662). At 6%
it gives **220–2,233 encounters (14–134 events)**, depending on the number of
parameters and the anticipated R².

**Recommendation:** restate §1.5 using the Riley criteria at the conservative
R² (15% of max), with the Phase 0 event rate:

> "Minimum n = 2,233 (134 events) for 14 candidate parameters at an event rate
> of 6%."

Keep the larger cohort as a target for precision and subgroup analysis, not
as the minimum.

### Phase 2 validation: the proposal understates it badly

The proposal's ~500 encounters / 30 events falls short on every criterion:

* **Collins 2016 (full text).** Prior guidance recommends that models "are
  evaluated with a minimum of 100 events". Just under half of published
  validation studies fall short of this. Their simulations support ≥ 100
  and preferably ≥ 200 events.
* **Riley 2021 (abstract).** Precise O/E, calibration slope, C statistic
  and net benefit are all required. In the authors' example (event
  proportion 0.018) the minimum was 9,835 participants (177 events),
  "driven by the calibration slope criterion, which we anticipate will often
  be the case".

`uri.samplesize` now implements the calibration-slope criterion. It assumes
the model's linear predictor is normally distributed, matched to the
anticipated event rate and C statistic, and targets a 95% CI width of 0.2
for the slope. A simulation with 300 validation sets confirmed the result:
the slope SD was 0.052 against a target of 0.051.

| Event rate | C | O/E | C precision | ≥ 100 events | Calibration slope | **Minimum** |
|---|---|---|---|---|---|---|
| 3% | 0.75 | 13,674 | 4,244 | 3,334 | 15,981 | **15,981** (480 events) |
| 6% | 0.75 | 6,626 | 2,126 | 1,667 | 8,763 | **8,763** (526 events) |
| 6% | 0.80 | 6,626 | 1,880 | 1,667 | 6,312 | **6,626** (398 events) |
| 10% | 0.75 | 3,807 | 1,322 | 1,000 | 5,819 | **5,819** (582 events) |

**Recommendation:** restate §2.5 roughly as:

> "Minimum n per Riley 2021, recalculated from the Phase 1 model's
> linear-predictor distribution (`python -m uri.samplesize --event-rate
> <rate> --lp <mean> <sd>`). Provisional estimate: ~6,600–8,800 encounters
> (~400–530 events) at 6%."

That is almost certainly multicenter. The sequential stopping rule can stay
as an interim safety look, but not as the sample-size justification. Net
benefit (Riley 2021's fourth criterion) is not implemented. In their example
it needed fewer patients than the slope criterion.

## Sources

| Reference | Used for | Access |
|---|---|---|
| Riley RD et al. Minimum sample size for developing a multivariable prediction model: Part II, binary and time-to-event outcomes. *Stat Med* 2019;38:1276–96. PMID 30357870. [doi:10.1002/sim.7992](https://doi.org/10.1002/sim.7992); correction [doi:10.1002/sim.8409](https://doi.org/10.1002/sim.8409) | Phase 1 criteria | Full text (PMC6519266) |
| Riley RD et al. Calculating the sample size required for developing a clinical prediction model. *BMJ* 2020;368:m441. PMID 32188600. [doi:10.1136/bmj.m441](https://doi.org/10.1136/bmj.m441) | Worked example (662) | Not in PMC; reproduced numerically |
| Riley RD et al. Minimum sample size for external validation of a clinical prediction model with a binary outcome. *Stat Med* 2021;40:4230–51. PMID 34031906. [doi:10.1002/sim.9025](https://doi.org/10.1002/sim.9025) | Phase 2 criteria | Abstract |
| Collins GS, Ogundimu EO, Altman DG. Sample size considerations for the external validation of a multivariable prognostic model. *Stat Med* 2016;35:214–26. PMID 26553135. [doi:10.1002/sim.6787](https://doi.org/10.1002/sim.6787) | ≥ 100 events | Full text (PMC4738418) |
| Quan H et al. Updating and validating the Charlson comorbidity index and score for risk adjustment in hospital discharge abstracts using data from 6 countries. *Am J Epidemiol* 2011;173:676–82. PMID 21330339. [doi:10.1093/aje/kwq433](https://doi.org/10.1093/aje/kwq433) | Charlson weights | Abstract |
| Charlson ME et al. A new method of classifying prognostic comorbidity in longitudinal studies: development and validation. *J Chronic Dis* 1987;40:373–83. PMID 3558716. [doi:10.1016/0021-9681(87)90171-8](https://doi.org/10.1016/0021-9681(87)90171-8) | Original Charlson weights | Abstract |
| Carey IM et al. Risk of infection in type 1 and type 2 diabetes compared with the general population: a matched cohort study. *Diabetes Care* 2018. PMID 29330152. [doi:10.2337/dc17-2131](https://doi.org/10.2337/dc17-2131) | Type 1 DM infection risk | Abstract |
| Patients with type 1 and type 2 diabetes hospitalized with COVID-19 in comparison with influenza: mortality and cardiorenal complications assessed by nationwide Swedish registry data. *Cardiovasc Diabetol* 2022. PMID 36522650. [doi:10.1186/s12933-022-01719-x](https://doi.org/10.1186/s12933-022-01719-x) | Type 1 DM in-hospital mortality | Abstract |
| Delgado MK et al. Development and validation of a risk score for unplanned ICU transfer in ED patients admitted to the ward. *J Hosp Med* 2013;8:13–9. PMID 23024040. [doi:10.1002/jhm.1979](https://doi.org/10.1002/jhm.1979) | ED-admission predictors | Abstract |
| Budnitz DS et al. Emergency hospitalizations for adverse drug events in older Americans. *N Engl J Med* 2011;365:2002–12. PMID 22111719. [doi:10.1056/NEJMsa1103053](https://doi.org/10.1056/NEJMsa1103053) | Medication classes | Abstract |
| Shehab N et al. US emergency department visits for outpatient adverse drug events, 2013–2014. *JAMA* 2016;316:2115–25. PMID 27893129. [doi:10.1001/jama.2016.16201](https://doi.org/10.1001/jama.2016.16201) | Medication classes | Full text (PMC6490178) |
