# Risk-factor registry

The registry follows the method on the "Step One: Rigorously Define the Risk
Factors" slide: define every candidate risk factor up front, record the
evidence for each one, and weight a factor only when the evidence supports
it. The source of truth is `uri/registry.py`, and this file mirrors it.

Every factor is **monitored** as soon as it appears in the EMR: it is listed
in the snapshot's `risk_factors` and can trigger correctable-cause rules. A
factor adds **points** only when it has evidence behind it:

| Status | Meaning | Contributes |
|---|---|---|
| `pooled_or` | Pooled odds ratio from a systematic review / meta-analysis | `ln(OR)`-scaled CRS points, plus the obstetric combined-odds panel |
| `v1_prior` | Point weight carried over from URI v1 (expert prior) | CRS points |
| `tracked` | Monitored, displayed, used by cause rules | nothing yet |
| `candidate` | Proposed; confirm against the source review | nothing yet |

## Weighting by pooled odds ratios

The five factors on the slide with the greatest confirmed risk are entered
with their pooled ORs. Odds ratios multiply, so log-odds add:

* **CRS obstetric domain** = min(6, 2 × Σ ln ORᵢ). Placenta previa/abruption
  alone gives 2 × ln 2.74 ≈ 2.0 points. All five together reach the 6-point
  cap.
* **Obstetric panel** reports the combined OR (Π ORᵢ). It also reports an
  estimated PPH risk, but only once `ob_baseline_pph_risk` has been set from
  local data. This calculation assumes the factors are independent, which
  they are not (previa and prolonged labor, for example), so it is
  uncalibrated until the review's multivariable estimates or local data
  replace it.

The obstetric module switches on for any O-chapter ICD-10 code, Z33/Z34/Z39/Z3A,
any obstetric factor, or `context.ob_status`. When it is on, the shock-index
threshold drops to 0.9, the QBL and fibrinogen signals turn on, and the
postpartum hemorrhage cause moves to the top of the correctable-cause list.

### Open items

1. **The other 10 definite/likely factors.** The slide shows only the top
   five of the review's 15. The `candidate` rows below are common PPH risk
   factors, listed so they are monitored now. Replace them with the review's
   actual list and pooled ORs.
2. **Citation.** Record the review's reference (from the slide's QR code) in
   `PPH_SLIDE` in `uri/registry.py`.
3. **Prior PPH coding.** A history of PPH is rarely coded. Z87.59 is a broad
   proxy, so an explicit history flag (OB intake form → `local_codes`) is
   better.
4. **QBL.** Quantitative blood loss is usually a local flowsheet row. Map it
   with `local_codes`, e.g. `{"urn:oid:…|QBL": "qbl_ml"}`.
5. **Extending the method beyond OB.** The `v1_prior` comorbidity points are
   expert priors. The same approach applies to them: a systematic review of
   ED decompensation risk factors would replace those points with pooled ORs.

## Obstetric hemorrhage

| ID | Factor | ICD-10-CM prefixes | CRS pts | Pooled OR | Status |
|---|---|---|---|---|---|
| `ob_previa_abruption` | Placenta previa / abruption | O44, O45 |  | 2.74 | pooled_or |
| `ob_uterine_rupture` | Uterine rupture | O710, O711 |  | 2.35 | pooled_or |
| `ob_prior_pph` | Prior postpartum hemorrhage | Z8759 |  | 2.25 | pooled_or |
| `ob_multiple_gestation` | Multiple gestation | O30 |  | 2.16 | pooled_or |
| `ob_prolonged_labor` | Prolonged labor | O63 |  | 1.76 | pooled_or |
| `ob_accreta` | Placenta accreta spectrum | O432 |  |  | candidate |
| `ob_chorioamnionitis` | Chorioamnionitis | O4112 |  |  | candidate |
| `ob_hypertensive` | Preeclampsia / HELLP | O11, O14 |  |  | candidate |
| `ob_prior_cesarean` | Prior cesarean delivery | O3421 |  |  | candidate |
| `ob_polyhydramnios` | Polyhydramnios | O40 |  |  | candidate |
| `ob_macrosomia` | Fetal macrosomia | O366 |  |  | candidate |
| `ob_fibroids` | Uterine fibroids in pregnancy | O341 |  |  | candidate |
| `ob_anemia` | Anemia in pregnancy | O990 |  |  | candidate |

## Comorbidities

| ID | Factor | ICD-10-CM prefixes | CRS pts | Pooled OR | Status |
|---|---|---|---|---|---|
| `chf` | CHF / HFrEF / HFpEF | I50 | 2.0 |  | v1_prior |
| `copd` | COPD / emphysema | J44, J43 | 2.0 |  | v1_prior |
| `cad` | CAD / prior MI | I25 | 1.5 |  | v1_prior |
| `ckd` | CKD / ESRD | N18, Z992 | 1.0 |  | v1_prior |
| `malignancy` | Active malignancy | C | 1.5 |  | v1_prior |
| `htn` | Hypertension | I10, I11, I12, I13, I15, I16 |  |  | tracked |
| `dm1` | Diabetes mellitus type 1 | E10 |  |  | tracked |
| `dm2` | Diabetes mellitus type 2 | E11 |  |  | tracked |
| `stroke_tia` | Stroke / TIA history | I63, G45, Z8673 |  |  | tracked |
| `dvt_pe` | DVT / PE history | Z8671, I26, I824 |  |  | tracked |
| `sepsis_prior` | Sepsis (prior / current) | A40, A41, R652 |  |  | tracked |
| `aud` | Alcohol use disorder | F10 |  |  | tracked |
| `obesity` | Obesity (BMI > 35) | E66, Z6835, Z6836, Z6837, Z6838, Z6839, Z684 |  |  | tracked |
| `dementia` | Dementia / cognitive impairment | F01, F02, F03, G30 |  |  | tracked |
| `immunosuppressant_tx` | Immunosuppressant therapy | Z796 |  |  | tracked |
| `afib` | Dysrhythmia (AF / flutter) | I48 |  |  | tracked |
| `asthma` | Asthma | J45 |  |  | tracked |
| `valvular` | Valvular heart disease | I05, I06, I07, I08, I34, I35, I36, I37 |  |  | tracked |
| `pulm_htn` | Pulmonary HTN / cor pulmonale | I27 |  |  | tracked |
| `cirrhosis` | Cirrhosis / hepatic failure | K703, K74, K72 |  |  | tracked |
| `immunocompromised` | Immunocompromised | D80, D81, D82, D83, D84 |  |  | tracked |
| `seizure` | Seizure disorder | G40 |  |  | tracked |
| `coagulopathy` | Coagulopathy | D65, D66, D67, D68 |  |  | tracked |
| `vent_dependent` | Prior intubation / vent dependent | Z9911 |  |  | tracked |
| `sud` | Substance use disorder | F11, F12, F13, F14, F15, F16, F18, F19 |  |  | tracked |
| `malnutrition` | Malnutrition / cachexia | E40, E41, E42, E43, E44, E45, E46, R64 |  |  | tracked |
| `frailty_dx` | Frailty | R54 |  |  | tracked |
| `transplant` | Transplant recipient | Z94 |  |  | tracked |
| `dysnatremia` | Hypo/hypernatremia history | E870, E871 |  |  | tracked |
| `adrenal_insufficiency` | Adrenal insufficiency | E271, E272, E274 |  |  | tracked |
| `anemia` | Anemia | D50, D51, D52, D53, D55, D56, D57, D58, D59, D60, D61, D62, D63, D64 |  |  | tracked |
| `chronic_opioid` | Chronic pain / opioid use | G89, Z79891 |  |  | tracked |
| `osa` | OSA / CPAP dependent | G4733 |  |  | tracked |
| `hiv` | HIV / AIDS | B20, Z21 |  |  | tracked |
| `aortic_aneurysm` | Known aortic aneurysm | I71 |  |  | tracked |
| `bone_joint_infection` | Septic arthritis / osteomyelitis | M00, M86 |  |  | tracked |
| `steroid_dependent` | Steroid dependent | Z7952 |  |  | tracked |
| `active_chemo` | Active chemotherapy | Z5111 |  |  | tracked |
| `radiation` | Active radiation therapy | Z510 |  |  | tracked |
| `hyperkalemia_hx` | Hyperkalemia history | E875 |  |  | tracked |
| `thyroid` | Thyroid disease | E01, E02, E03, E04, E05, E06, E07 |  |  | tracked |
| `gi_bleed` | GI bleed (recent / active) | K920, K921, K922, K625, I8511 |  |  | tracked |
| `psych` | Psychiatric comorbidity | F2, F3, F40, F41, F43 |  |  | tracked |
| `neuromuscular` | Neuromuscular disease | G12, G35, G70, G71 |  |  | tracked |
| `tb_infectious` | TB / active infectious disease | A15, A16, A17, A18, A19 |  |  | tracked |
| `hardware_line` | Indwelling hardware / line | Z95, Z96, Z452 |  |  | tracked |
| `dnr` | DNR / DNI | Z66 |  |  | tracked |
| `homeless` | No fixed address | Z590 |  |  | tracked |
| `nonadherence` | Medication non-adherence | Z911 |  |  | tracked |
| `dialysis` | Dialysis patient | Z992, Z49 |  |  | tracked |
| `pregnancy` | Active pregnancy / postpartum | O, Z33, Z34, Z39, Z3A |  |  | tracked |

## Medication classes

| ID | Factor | Ingredients | CRS pts | Pooled OR | Status |
|---|---|---|---|---|---|
| `anticoagulant` | Anticoagulants | warfarin, apixaban, rivaroxaban, dabigatran, edoxaban, heparin, enoxaparin, dalteparin, fondaparinux | 1.0 |  | v1_prior |
| `insulin` | Insulin | insulin | 1.5 |  | v1_prior |
| `opioid` | Opioids | morphine, hydromorphone, fentanyl, oxycodone, hydrocodone, methadone, tramadol, oxymorphone, tapentadol, buprenorphine | 1.0 |  | v1_prior |
| `diuretic` | Diuretics (loop / thiazide) | furosemide, bumetanide, torsemide, hydrochlorothiazide, chlorthalidone, metolazone |  |  | tracked |
| `antiplatelet` | Antiplatelets | aspirin, clopidogrel, prasugrel, ticagrelor |  |  | tracked |
| `beta_blocker` | Beta-blockers | metoprolol, atenolol, carvedilol, propranolol, labetalol, bisoprolol |  |  | tracked |
| `acei_arb` | ACE-I / ARB | lisinopril, enalapril, ramipril, losartan, valsartan, irbesartan |  |  | tracked |
| `sglt2i` | SGLT2 inhibitors | empagliflozin, dapagliflozin, canagliflozin, ertugliflozin |  |  | tracked |
| `sulfonylurea` | Sulfonylureas | glipizide, glyburide, glimepiride |  |  | tracked |
| `corticosteroid` | Corticosteroids (systemic) | prednisone, prednisolone, methylprednisolone, dexamethasone, hydrocortisone |  |  | tracked |
| `immunosuppressant` | Immunosuppressants | tacrolimus, cyclosporine, mycophenolate, azathioprine, sirolimus |  |  | tracked |
| `benzodiazepine` | Benzodiazepines | lorazepam, diazepam, alprazolam, clonazepam, midazolam |  |  | tracked |
| `antipsychotic` | Antipsychotics | haloperidol, quetiapine, olanzapine, risperidone |  |  | tracked |
| `digoxin` | Digoxin | digoxin |  |  | tracked |
| `lithium` | Lithium | lithium |  |  | tracked |
| `methotrexate` | Methotrexate | methotrexate |  |  | tracked |
| `antiarrhythmic` | Antiarrhythmics | amiodarone, sotalol, flecainide, dofetilide |  |  | tracked |
| `vasoactive` | Vasopressors / vasodilators | norepinephrine, epinephrine, vasopressin, phenylephrine, dopamine, dobutamine, nitroglycerin, nicardipine |  |  | tracked |
| `nephrotoxin` | Nephrotoxins | gentamicin, tobramycin, amikacin, vancomycin, ibuprofen, ketorolac, naproxen |  |  | tracked |
