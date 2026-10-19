# Risk-factor registry

Scope: decompensation in adult emergency department and critical-care
patients. The registry starts from the risk factors defined in URI v1 (the
Patient Input sheet's medical-history, medication and laboratory panels) and
expands detection and scoring around them. The source of truth is
`uri/registry.py`, and this file mirrors it.

Every factor is **monitored** as soon as it appears in the EMR: it is listed
in the snapshot's `risk_factors` and can trigger correctable-cause rules. A
factor adds **points** only when evidence backs it:

| Status | Meaning | Contributes |
|---|---|---|
| `validated_index` | Category of a published, validated index (Charlson, Quan ICD-10 coding) | Charlson points → CRS2 comorbidity (capped at 6) |
| `v1_prior` | Point weight carried over from URI v1 (expert prior) | v1 CRS; medication points in CRS2 |
| `tracked` | Monitored, displayed, used by cause rules and Padua | nothing directly; the fitted model can weight it |
| `pooled_or` | Pooled odds ratio from a systematic review | `k × ln(OR)` points (none in use yet; see below) |

## Coverage of the v1 panels

| v1 panel | v1 items | Detected from the EMR |
|---|---|---|
| Medical history | 60 | All 60. Most by ICD-10-CM. Nine needed other data: recent surgery (Procedure), recent hospitalization (Encounter), polypharmacy (medication count), high-dose diuretic (dose), language barrier (Patient.communication), advance directive (Consent), PE risk (Padua score), frailty (CFS flowsheet), ALC and poor social support (Z codes). |
| High-risk medications | 28 classes | All 28, as 30 registry classes (oral hypoglycemics split into SGLT2 inhibitors / sulfonylureas; vasopressors split from vasodilators) |
| Laboratory | 36 items | All 33 numeric items are used: scored (NEWS2/SOFA/CRS2), trended (repeat lactate, serial troponin, Hgb, creatinine), in cause rules, or in the critical-value panel (`uri/labs.py`). HbA1c is tracked as glucose context. The 3 order/qualitative items (blood cultures, UA, urine hCG) are not scored. |

### Derived factors

| Factor | Derivation |
|---|---|
| `recent_surgery` | FHIR Procedure with surgical category (SNOMED 387713003) performed ≤ 30 d before the current clock |
| `recent_hospitalization` | FHIR Encounter, inpatient class, finished ≤ 90 d before the current clock |
| `polypharmacy` | > 5 distinct active medications, mapped or not, counted once per ingredient |
| `high_dose_diuretic` | single loop-diuretic dose ≥ 80 mg furosemide-equivalent (bumetanide 1 mg = torsemide 20 mg = furosemide 40 mg) |
| `language_barrier` | preferred language not English, or the interpreter-required extension |
| `advance_directive` | Consent with an advance-directive category |
| `frailty_dx` | ICD-10 R54, or Clinical Frailty Scale ≥ 5 |
| `pe_risk` | Padua Prediction Score ≥ 4 (Barbar 2010), built from registry factors and age. Reduced mobility and hormonal therapy are not in the stream, so it can under-score. |

## Comorbidity: Charlson replaces the v1 points

v1 gave CHF, COPD, CAD, CKD and malignancy hand-picked points. v2 uses the
Charlson Comorbidity Index with the Quan 2005 ICD-10 coding algorithm and the
Quan 2011 weights, with hierarchies applied (complicated over uncomplicated
diabetes, severe over mild liver disease, metastatic over any malignancy).
The v1 points are still used, but only in `uri_v1`.

## Adding evidence-weighted factors

When a systematic review supplies pooled odds ratios for ED/ICU
decompensation risk factors:

1. Set `odds_ratio=…, status="pooled_or", source="…"` on the factors in `uri/registry.py`.
2. Add a CRS2 domain that sums `ln(OR)` over present factors (odds ratios multiply, so log-odds add).
3. Re-run `uri.calibrate`; the fitted model's coefficients are the local, adjusted log odds ratios.

## Charlson categories

| ID | Factor | Detection | Charlson wt | Status |
|---|---|---|---|---|
| `cci_mi` | Myocardial infarction | I21, I22, I252 | 0 | validated_index |
| `cci_chf` | Congestive heart failure | I099, I110, I130, I132, I255, I420, I425, I426, I427, I428, I429, I43, I50, P290 | 2 | validated_index |
| `cci_pvd` | Peripheral vascular disease | I70, I71, I731, I738, I739, I771, I790, I792, K551, K558, K559, Z958, Z959 | 0 | validated_index |
| `cci_cvd` | Cerebrovascular disease | G45, G46, H340, I6 | 0 | validated_index |
| `cci_dementia` | Dementia | F00, F01, F02, F03, F051, G30, G311 | 2 | validated_index |
| `cci_pulmonary` | Chronic pulmonary disease | I278, I279, J40, J41, J42, J43, J44, J45, J46, J47, J60, J61, J62, J63, J64, J65, J66, … | 1 | validated_index |
| `cci_rheumatic` | Rheumatic disease | M05, M06, M315, M32, M33, M34, M351, M353, M360 | 1 | validated_index |
| `cci_pud` | Peptic ulcer disease | K25, K26, K27, K28 | 0 | validated_index |
| `cci_liver_mild` | Mild liver disease | B18, K700, K701, K702, K703, K709, K713, K714, K715, K717, K73, K74, K760, K762, K763, … | 2 | validated_index |
| `cci_dm` | Diabetes without chronic complication | E100, E101, E106, E108, E109, E110, E111, E116, E118, E119, E130, E131, E136, E138, E139 | 0 | validated_index |
| `cci_dm_complicated` | Diabetes with chronic complication | E102, E103, E104, E105, E107, E112, E113, E114, E115, E117, E132, E133, E134, E135, E137 | 1 | validated_index |
| `cci_plegia` | Hemiplegia / paraplegia | G041, G114, G801, G802, G81, G82, G830, G831, G832, G833, G834, G839 | 2 | validated_index |
| `cci_renal` | Renal disease | I120, I131, N032, N033, N034, N035, N036, N037, N052, N053, N054, N055, N056, N057, N18… | 1 | validated_index |
| `cci_malignancy` | Any malignancy (incl. lymphoma, leukemia) | C00, C01, C02, C03, C04, C05, C06, C07, C08, C09, C10, C11, C12, C13, C14, C15, C16, C1… | 2 | validated_index |
| `cci_liver_severe` | Moderate / severe liver disease | I850, I859, I864, I982, K704, K711, K721, K729, K765, K766, K767 | 4 | validated_index |
| `cci_metastatic` | Metastatic solid tumor | C77, C78, C79, C80 | 6 | validated_index |
| `cci_hiv` | AIDS / HIV | B20, B21, B22, B24 | 4 | validated_index |

## Medical history (v1 panel)

| ID | Factor | Detection | v1 CRS pts | Status |
|---|---|---|---|---|
| `chf` | CHF / HFrEF / HFpEF | I50 | 2.0 | v1_prior |
| `copd` | COPD / emphysema | J44, J43 | 2.0 | v1_prior |
| `cad` | CAD / prior MI | I25 | 1.5 | v1_prior |
| `ckd` | CKD / ESRD | N18, Z992 | 1.0 | v1_prior |
| `malignancy` | Active malignancy | C | 1.5 | v1_prior |
| `htn` | Hypertension | I10, I11, I12, I13, I15, I16 |  | tracked |
| `dm1` | Diabetes mellitus type 1 | E10 |  | tracked |
| `dm2` | Diabetes mellitus type 2 | E11 |  | tracked |
| `stroke_tia` | Stroke / TIA history | I63, G45, Z8673 |  | tracked |
| `dvt_pe` | DVT / PE history | Z8671, I26, I824 |  | tracked |
| `sepsis_prior` | Sepsis (prior / current) | A40, A41, R652 |  | tracked |
| `aud` | Alcohol use disorder | F10 |  | tracked |
| `obesity` | Obesity (BMI > 35) | E66, Z6835, Z6836, Z6837, Z6838, Z6839, Z684 |  | tracked |
| `dementia` | Dementia / cognitive impairment | F01, F02, F03, G30 |  | tracked |
| `immunosuppressant_tx` | Immunosuppressant therapy | Z796 |  | tracked |
| `afib` | Dysrhythmia (AF / flutter) | I48 |  | tracked |
| `asthma` | Asthma | J45 |  | tracked |
| `valvular` | Valvular heart disease | I05, I06, I07, I08, I34, I35, I36, I37 |  | tracked |
| `pulm_htn` | Pulmonary HTN / cor pulmonale | I27 |  | tracked |
| `cirrhosis` | Cirrhosis / hepatic failure | K703, K74, K72 |  | tracked |
| `immunocompromised` | Immunocompromised | D80, D81, D82, D83, D84 |  | tracked |
| `seizure` | Seizure disorder | G40 |  | tracked |
| `coagulopathy` | Coagulopathy | D65, D66, D67, D68 |  | tracked |
| `vent_dependent` | Prior intubation / vent dependent | Z9911 |  | tracked |
| `sud` | Substance use disorder | F11, F12, F13, F14, F15, F16, F18, F19 |  | tracked |
| `malnutrition` | Malnutrition / cachexia | E40, E41, E42, E43, E44, E45, E46, R64 |  | tracked |
| `frailty_dx` | Frailty | R54 |  | tracked |
| `transplant` | Transplant recipient | Z94 |  | tracked |
| `dysnatremia` | Hypo/hypernatremia history | E870, E871 |  | tracked |
| `adrenal_insufficiency` | Adrenal insufficiency | E271, E272, E274 |  | tracked |
| `anemia` | Anemia | D50, D51, D52, D53, D55, D56, D57, D58, D59, D60, D61, D62, D63, D64 |  | tracked |
| `chronic_opioid` | Chronic pain / opioid use | G89, Z79891 |  | tracked |
| `osa` | OSA / CPAP dependent | G4733 |  | tracked |
| `hiv` | HIV / AIDS | B20, Z21 |  | tracked |
| `aortic_aneurysm` | Known aortic aneurysm | I71 |  | tracked |
| `bone_joint_infection` | Septic arthritis / osteomyelitis | M00, M86 |  | tracked |
| `steroid_dependent` | Steroid dependent | Z7952 |  | tracked |
| `active_chemo` | Active chemotherapy | Z5111 |  | tracked |
| `radiation` | Active radiation therapy | Z510 |  | tracked |
| `hyperkalemia_hx` | Hyperkalemia history | E875 |  | tracked |
| `thyroid` | Thyroid disease | E01, E02, E03, E04, E05, E06, E07 |  | tracked |
| `gi_bleed` | GI bleed (recent / active) | K920, K921, K922, K625, I8511 |  | tracked |
| `psych` | Psychiatric comorbidity | F2, F3, F40, F41, F43 |  | tracked |
| `neuromuscular` | Neuromuscular disease | G12, G35, G70, G71 |  | tracked |
| `tb_infectious` | TB / active infectious disease | A15, A16, A17, A18, A19 |  | tracked |
| `hardware_line` | Indwelling hardware / line | Z95, Z96, Z452 |  | tracked |
| `dnr` | DNR / DNI | Z66 |  | tracked |
| `homeless` | No fixed address | Z590 |  | tracked |
| `nonadherence` | Medication non-adherence | Z911 |  | tracked |
| `dialysis` | Dialysis patient | Z992, Z49 |  | tracked |
| `pregnancy` | Active pregnancy / postpartum | O, Z33, Z34, Z39, Z3A |  | tracked |
| `recent_surgery` | Recent surgery (< 30 days) | derived (see above) |  | tracked |
| `recent_hospitalization` | Recent hospitalization (< 90 days) | derived (see above) |  | tracked |
| `polypharmacy` | Polypharmacy (> 5 active medications) | derived (see above) |  | tracked |
| `high_dose_diuretic` | High-dose loop diuretic | derived (see above) |  | tracked |
| `language_barrier` | Language barrier | derived (see above) |  | tracked |
| `advance_directive` | Advance directive on file | derived (see above) |  | tracked |
| `alc` | ALC / awaiting placement | Z751 |  | tracked |
| `poor_social_support` | Poor social support | Z602, Z636, Z742 |  | tracked |
| `thrombophilia` | Thrombophilia | D685, D686 |  | tracked |
| `pe_risk` | VTE risk: Padua score >= 4 | derived (see above) |  | tracked |

## Medication classes (v1 panel)

| ID | Factor | Ingredients | v1 CRS pts | Status |
|---|---|---|---|---|
| `anticoagulant` | Anticoagulants | warfarin, apixaban, rivaroxaban, dabigatran, edoxaban, heparin, enoxaparin, dalteparin,… | 1.0 | v1_prior |
| `insulin` | Insulin | insulin | 1.5 | v1_prior |
| `opioid` | Opioids | morphine, hydromorphone, fentanyl, oxycodone, hydrocodone, methadone, tramadol, oxymorp… | 1.0 | v1_prior |
| `diuretic` | Diuretics (loop / thiazide) | furosemide, bumetanide, torsemide, hydrochlorothiazide, chlorthalidone, metolazone |  | tracked |
| `antiplatelet` | Antiplatelets | aspirin, clopidogrel, prasugrel, ticagrelor |  | tracked |
| `beta_blocker` | Beta-blockers | metoprolol, atenolol, carvedilol, propranolol, labetalol, bisoprolol |  | tracked |
| `acei_arb` | ACE-I / ARB | lisinopril, enalapril, ramipril, losartan, valsartan, irbesartan |  | tracked |
| `sglt2i` | SGLT2 inhibitors | empagliflozin, dapagliflozin, canagliflozin, ertugliflozin |  | tracked |
| `sulfonylurea` | Sulfonylureas | glipizide, glyburide, glimepiride |  | tracked |
| `corticosteroid` | Corticosteroids (systemic) | prednisone, prednisolone, methylprednisolone, dexamethasone, hydrocortisone |  | tracked |
| `immunosuppressant` | Immunosuppressants | tacrolimus, cyclosporine, mycophenolate, azathioprine, sirolimus |  | tracked |
| `benzodiazepine` | Benzodiazepines | lorazepam, diazepam, alprazolam, clonazepam, midazolam |  | tracked |
| `antipsychotic` | Antipsychotics | haloperidol, quetiapine, olanzapine, risperidone |  | tracked |
| `digoxin` | Digoxin | digoxin |  | tracked |
| `lithium` | Lithium | lithium |  | tracked |
| `methotrexate` | Methotrexate | methotrexate |  | tracked |
| `antiarrhythmic` | Antiarrhythmics | amiodarone, sotalol, flecainide, dofetilide |  | tracked |
| `vasopressor` | Vasopressors / inotropes | norepinephrine, epinephrine, vasopressin, phenylephrine, dopamine, dobutamine, milrinon… |  | tracked |
| `vasodilator` | Vasodilators (IV) | nitroglycerin, nicardipine, nitroprusside, clevidipine |  | tracked |
| `nsaid` | NSAIDs | ibuprofen, ketorolac, naproxen, diclofenac, meloxicam, indomethacin, celecoxib |  | tracked |
| `aminoglycoside` | Aminoglycosides | gentamicin, tobramycin, amikacin |  | tracked |
| `nephrotoxin` | Other nephrotoxins | vancomycin, amphotericin, polymyxin, colistin, foscarnet, cidofovir |  | tracked |
| `antiepileptic` | Antiepileptics | levetiracetam, phenytoin, valproate, valproic, carbamazepine, lamotrigine, lacosamide, … |  | tracked |
| `ppi` | Proton-pump inhibitors | omeprazole, pantoprazole, esomeprazole, lansoprazole, rabeprazole |  | tracked |
| `chemotherapy` | Chemotherapy agents | cyclophosphamide, doxorubicin, cisplatin, carboplatin, oxaliplatin, paclitaxel, docetax… |  | tracked |
| `biologic` | Biologic agents / mAbs | mab , infliximab, adalimumab, rituximab, tocilizumab, pembrolizumab, nivolumab, etanerc… |  | tracked |
| `thyroid_med` | Thyroid medications | levothyroxine, liothyronine, methimazole, propylthiouracil |  | tracked |
| `tca` | Tricyclic antidepressants | amitriptyline, nortriptyline, imipramine, doxepin, desipramine, clomipramine |  | tracked |
| `serotonergic` | Serotonergic agents | sertraline, fluoxetine, citalopram, escitalopram, paroxetine, venlafaxine, duloxetine, … |  | tracked |
| `maoi` | MAOIs | phenelzine, tranylcypromine, isocarboxazid, selegiline, rasagiline |  | tracked |
