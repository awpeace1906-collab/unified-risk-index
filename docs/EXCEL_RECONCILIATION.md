# Reconciliation with the v1 spreadsheet

`EM_Decompensation_Risk_Tool_1.xlsx` is the reference implementation of URI v1.
Porting it formula by formula turned up cell references that point at the
wrong rows, most likely because rows were inserted after the formulas were
written. The streaming engine implements what the manuscripts and the
spreadsheet labels describe, not the broken references. Each item below needs a
PI decision before Phase 1, because the derivation paper has to describe one
definitive scoring.

## Bugs in the v1 workbook (Risk Dashboard sheet)

| # | Where | What the formula does | What v2 does |
|---|---|---|---|
| 1 | Time windows `C36:C40` | Multiplies **E81 (normalized qSOFA, 0–1)** by 1.35…0.75, so the 6–48 h estimates are always 0–1%. | Multiplies the URI (0–100), as the manuscript describes. |
| 2 | MEWS rows `D66:D71` | Read `C63:C68` (empty/header rows), so every MEWS parameter scores 0. There is no MEWS total row; the URI reads `D69` (the temperature points) as "MEWS", and `G82` tests `D72` (empty). | Full MEWS total, normalized by 14. |
| 3 | CRS metabolic `D25` | pH from `C43` (WBC) and lactate from `C41` (header). | pH and lactate. |
| 4 | CRS renal `D26` | Creatinine from `C45` (platelets). | Creatinine. |
| 5 | CRS hematologic `D27` | Hemoglobin from `C47` (BUN). | Hemoglobin. |
| 6 | CRS cardiac `D28` | Troponin from `C51` (pH), BNP from `C52` (pCO₂). Troponin threshold 0.04 is in ng/mL; the input row is labelled ng/L. | Troponin and BNP; troponin compared with `troponin_url_ng_l` (default 14 ng/L, set to your assay's 99th percentile). NT-proBNP > 1800 pg/mL accepted as an alternative to BNP > 400. |
| 7 | CRS comorbidity `D29` | Scores `C28` (DM type 1) where the manuscript lists CKD. | CKD = 1 point. **Confirm.** Evidence supports CKD ([PI_DECISIONS_EVIDENCE.md](PI_DECISIONS_EVIDENCE.md)). |
| 8 | CRS medication `D30` | Reads `C70` (thyroid meds), `C72`, `C73` (empty). | Reconstructed from the label "Anticoag, diuretics, insulin, opioids": anticoagulant 1, insulin 1.5, opioid 1. Diuretics are monitored but unweighted, since the formula had only three terms. **Confirm.** ADE surveillance ranks anticoagulants first, so the evidence suggests anticoagulant 1.5 ([PI_DECISIONS_EVIDENCE.md](PI_DECISIONS_EVIDENCE.md)). |

## Deliberate departures from the workbook's scoring

| # | Item | Workbook | v2 | Why |
|---|---|---|---|---|
| 9 | NEWS2 RR 9–11 | 0 points | 1 point | RCP NEWS2 chart. |
| 10 | NEWS2 temperature | °F bands; ≤ 95 °F → 2, 102.4–104 °F → 1 | °C bands per RCP: ≤ 35.0 → 3, ≥ 39.1 → 2 | RCP NEWS2 chart. |
| 11 | NEWS2 consciousness | Graded 0–3 from GCS | Any non-Alert (new confusion, V, P, U) → 3 | RCP NEWS2 chart. GCS < 15 is treated as non-Alert when no AVPU/ACVPU is charted. |
| 12 | NEWS2 supplemental O₂ | Omitted, though the score is normalized by 20 (which assumes the O₂ points) | +2 when on O₂ (FiO₂ > 21% or any O₂ flow). SpO₂ Scale 2 used when `hypercapnic_target` is set. | RCP NEWS2; streamed flowsheets carry O₂ delivery. |
| 13 | qSOFA mentation | GCS < 14 | GCS < 15 / non-Alert | Sepsis-3 definition. The Scoring Reference sheet already says GCS < 15. |
| 14 | MEWS bands | HR ≤ 40 → 3, 41–50 → 2; RR ≤ 8 → 3; urine output scored | Subbe 2001: HR < 41 → 2, 41–50 → 1; RR < 9 → 2; urine output not scored | The manuscripts cite the published MEWS performance (AUROC 0.64–0.76). Scoring a variant would undercut that citation. |
| 15 | MEWS toggle default | `NO` | Included (`include_mews: true`) | MEWS was optional because of urine output, which v2 does not need. All five Subbe parameters come in with every vitals set. Set `include_mews: false` to reproduce the 50/30/20 weighting. |
| 16 | Risk bands | Integer cut points (0–20, 21–40, …) | Same cut points, applied to the URI rounded to an integer | Avoids gaps such as 20.5. |

## Unchanged from v1 (still priors to be re-derived in Phase 1)

- Component weights 40/25/20/15 (and 50/30/20 without MEWS)
- Normalization maxima NEWS2 20, qSOFA 3, MEWS 14, CRS 30
- CRS sub-domain points: NEWS2 × 0.22, qSOFA × 4, and the lab, comorbidity and frailty points
- Time-window multipliers ×1.35 / 1.20 / 1.05 / 0.90 / 0.75, capped at 99%
- The 15 correctable causes, their actions and their time sensitivities
