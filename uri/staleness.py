"""Per-result staleness rules: how long a value stays current.

Different results lose relevance at different rates, so one window for every
lab (and one for every vital) is replaced by a rule per result, anchored to
the re-measurement interval in a guideline or scoring system where one
exists. Vital signs follow the NEWS2 minimum monitoring frequency, so their
window shortens as the patient's NEWS2 rises.

EVERY WINDOW HERE IS STILL A PLACEHOLDER. Status levels:
  UNVERIFIED          no checkable source yet (expert placeholder)
  CITATION_CONFIRMED  the cited paper was found in PubMed (PMID / DOI below),
                      but the window value is not stated in what PubMed
                      exposes (abstract unavailable or silent) and must be
                      checked against the full text
  NOT_IN_PUBMED       the source is not indexed in PubMed (e.g. the RCP NEWS2
                      report); check the source document directly
  VERIFIED            window confirmed against full text retrieved from
                      PubMed Central
Each rule carries a PubMed query for the remaining work.
`python -m uri.staleness` prints the list (docs/STALENESS_RULES.md, kept in
sync by a test).

A stale value is still used for scoring within the current visit
(Config.labs_hold_through_episode). Staleness is reported in the snapshot,
and "hours since measured" is a model feature, so Phase 1 calibration can
estimate from outcomes how much an older value still predicts.
"""

from __future__ import annotations

from dataclasses import dataclass

UNVERIFIED = "unverified: confirm via PubMed"
CITATION_CONFIRMED = "citation confirmed in PubMed; window to check against full text"
NOT_IN_PUBMED = "source not indexed in PubMed; check source document"
VERIFIED = "verified"
STATUSES = (UNVERIFIED, CITATION_CONFIRMED, NOT_IN_PUBMED, VERIFIED)
CHECKED_ON = "2026-10-20"  # PubMed search, article metadata, and PMC full text where available


@dataclass(frozen=True)
class Rule:
    params: tuple[str, ...]
    window_h: float | None      # None: window depends on context (see window())
    basis: str                  # what the window is anchored to
    source: str                 # citation as currently understood (unverified)
    pubmed_query: str
    status: str = UNVERIFIED
    pmids: tuple[str, ...] = ()
    dois: tuple[str, ...] = ()
    notes: str = ""


VITALS = ("hr", "rr", "sbp", "dbp", "map", "spo2", "temp_c", "gcs", "avpu", "o2_supplemental", "fio2",
          "o2_flow", "resp_support", "etco2", "pain", "urine_ml_hr")

# NEWS2 clinical response: minimum observation frequency by score
NEWS2_FREQUENCY_H = [  # (condition, hours)
    ("NEWS2 >= 7", 0.5),            # continuous monitoring; flag after 30 min
    ("NEWS2 5-6", 1.0),
    ("single parameter = 3", 1.0),
    ("NEWS2 1-4", 4.0),             # "minimum 4-6 hourly": the stricter bound
    ("NEWS2 0", 12.0),
]

RULES: list[Rule] = [
    Rule(VITALS, None,
         "NEWS2 minimum monitoring frequency: 0 -> 12-hourly; 1-4 -> 4-6-hourly; 5-6 or any single "
         "parameter of 3 -> hourly; >= 7 -> continuous",
         "Royal College of Physicians. National Early Warning Score (NEWS) 2. London: RCP, 2017",
         '"National Early Warning Score 2" AND (monitoring frequency OR observation frequency)',
         status=NOT_IN_PUBMED,
         notes="RCP report is grey literature; PubMed papers (PMID 30287428; Williams 2022 PMID 36427887, "
               "abstract only in PMC) confirm NEWS2 is the 2017 RCP update but do not reproduce the "
               "monitoring-frequency chart"),
    Rule(("lactate",), None,
         "re-measure within 2-4 h when > 2 mmol/L (4 h used); 24 h when normal",
         "Levy MM, Evans LE, Rhodes A. The Surviving Sepsis Campaign Bundle: 2018 update. "
         "Crit Care Med 2018;46:997-1000",
         '"Surviving Sepsis Campaign" bundle 2018 update lactate remeasure',
         status=CITATION_CONFIRMED, pmids=("29767636", "34599691"),
         dois=("10.1097/CCM.0000000000003119", "10.1007/s00134-021-06506-y"),
         notes="2018 bundle not in PMC (2-4 h interval unchecked). SSC 2021 full text (PMC8486643) recommends "
               "guiding resuscitation by decreasing lactate but states no re-measurement interval; it "
               "describes ANDROMEDA-SHOCK measuring lactate every 2 h for the first 8 h of septic shock, "
               "consistent with a short window"),
    Rule(("troponin_hs",), 6,
         "serial sampling at 0/1 h or 0/2 h (0/3 h) for myocardial infarction rule-in/out; 6 h outer bound",
         "Collet JP et al. 2020 ESC Guidelines for the management of acute coronary syndromes in patients "
         "presenting without persistent ST-segment elevation. Eur Heart J 2021;42:1289-1367",
         "ESC 0/1h algorithm high-sensitivity troponin NSTE-ACS guideline 2020",
         status=CITATION_CONFIRMED, pmids=("32860058",), dois=("10.1093/eurheartj/ehaa575",),
         notes="abstract not available and no PMC full text; check the 0/1 h and 0/2 h sampling times in "
               "the guideline itself"),
    Rule(("ph", "pco2", "po2", "hco3"), 6,
         "blood gases change within hours; no guideline re-measurement interval identified",
         "expert placeholder", "arterial blood gas repeat interval critically ill timing"),
    Rule(("glucose",), 6,
         "glucose changes within hours (insulin, dextrose); no single guideline interval",
         "expert placeholder", "glucose monitoring frequency hospitalized critically ill"),
    Rule(("k", "na", "cl", "anion_gap", "mg", "phos"), 12,
         "electrolytes can shift within hours after treatment; routine re-check usually 12-24 h",
         "expert placeholder", "electrolyte monitoring frequency critically ill potassium repletion recheck"),
    Rule(("plt", "bilirubin", "creatinine", "bun"), 24,
         "SOFA scores the worst value in each 24 h period; KDIGO judges creatinine change over 48 h / 7 d",
         "Vincent JL et al. The SOFA (Sepsis-related Organ Failure Assessment) score to describe organ "
         "dysfunction/failure. Intensive Care Med 1996;22:707-10; Khwaja A. KDIGO clinical practice "
         "guidelines for acute kidney injury. Nephron Clin Pract 2012;120:c179-84",
         "Sequential Organ Failure Assessment score original description 1996 Vincent",
         status=VERIFIED, pmids=("8844239", "31775846", "22890468", "23394211"),
         dois=("10.1007/BF01709751", "10.1186/s13054-019-2663-7", "10.1159/000339789", "10.1186/cc11454"),
         notes="SOFA: Lambden 2019 full text (PMC6880479): the sub-score uses 'the most severe (worst) value "
               "for the respective 24-h period', as in the original validation. KDIGO: Kellum 2013 full text "
               "(PMC4057151) confirms a >= 0.3 mg/dl rise 'within a 48-hour period' and stages 1.5-1.9x / "
               "2.0-2.9x / >= 3.0x or >= 4.0 mg/dl; the 7-day criterion is in a bulleted definition the PMC "
               "text omits (unchecked)"),
    Rule(("hgb",), None,
         "24 h when stable; 6 h while a drop is under way (active bleeding)",
         "expert placeholder", "hemoglobin monitoring interval acute gastrointestinal bleeding"),
    Rule(("wbc", "inr", "fibrinogen"), 24,
         "daily laboratory cycle; no guideline interval identified", "expert placeholder",
         "routine daily laboratory testing intensive care unit frequency"),
    Rule(("bnp", "ntprobnp", "procalcitonin", "crp", "ddimer"), 48,
         "trended over days; procalcitonin-guided antibiotic protocols re-measure every 24-48 h",
         "Schuetz P et al. Procalcitonin to initiate or discontinue antibiotics in acute respiratory tract "
         "infections. Cochrane Database Syst Rev 2017;10:CD007498",
         "procalcitonin guided antibiotic therapy remeasurement interval Cochrane",
         status=CITATION_CONFIRMED, pmids=("29025194",), dois=("10.1002/14651858.CD007498.pub3",),
         notes="PMC6485408 returned the abstract only, which states no re-measurement interval; check the "
               "trial algorithms in the Cochrane full text. BNP / CRP / D-dimer windows remain expert "
               "placeholders"),
    Rule(("lipase", "alt", "ast", "ammonia", "cortisol", "tsh"), 72,
         "diagnostic rather than trended in this window", "expert placeholder",
         "liver enzyme ammonia repeat testing interval inpatient"),
    Rule(("hba1c",), 24 * 90,
         "reflects about 3 months of glycaemia", "expert placeholder", "HbA1c reflects glycemia three months"),
]

_BY_PARAM = {p: r for r in RULES for p in r.params}


def rule_for(param: str) -> Rule | None:
    return _BY_PARAM.get(param)


def news2_window(news2_total: float | None, single_three: bool) -> tuple[float, str]:
    if news2_total is None:
        return 12.0, "NEWS2 unknown"
    if news2_total >= 7:
        return 0.5, "NEWS2 >= 7"
    if news2_total >= 5:
        return 1.0, "NEWS2 5-6"
    if single_three:
        return 1.0, "single parameter = 3"
    if news2_total >= 1:
        return 4.0, "NEWS2 1-4"
    return 12.0, "NEWS2 0"


def window(param: str, values: dict, news2_total: float | None = None, single_three: bool = False,
           hgb_dropping: bool = False) -> tuple[float, str] | None:
    """(hours a value stays current, why) for ``param`` given the current
    clinical state, or None if no rule covers it."""
    r = rule_for(param)
    if r is None:
        return None
    if param in VITALS:
        return news2_window(news2_total, single_three)
    if param == "lactate":
        lac = values.get("lactate")
        return (4.0, "lactate > 2") if lac is not None and lac > 2 else (24.0, "lactate <= 2")
    if param == "hgb":
        return (6.0, "Hgb falling") if hgb_dropping else (24.0, "Hgb stable")
    return float(r.window_h), r.basis


def render() -> str:
    lines = [
        "# Staleness rules (placeholders awaiting PubMed confirmation)",
        "",
        "Generated by `python -m uri.staleness` from `uri/staleness.py`; do not edit by hand.",
        "",
        "These per-result windows decide when a value is reported as stale. They replace the single",
        "24 h (labs) / 2 h (vitals) windows. **Every window is still a placeholder.**",
        "",
        f"PubMed check ({CHECKED_ON}): citations looked up by search and article metadata, and full text",
        "pulled from PubMed Central where available (PMC holds only open-access papers). *Verified* means",
        "the window was confirmed in that full text. *Citation confirmed* means the paper exists and is",
        "cited correctly, but the window value could not be read (paywalled, abstract only, or not stated).",
        "Expert placeholders have no source yet.",
        "",
        "| Results | Window | Basis | Source | PubMed | Status | Still to do / query |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in RULES:
        if r.params == VITALS:
            win = "by NEWS2: " + "; ".join(f"{c} {h:g} h" for c, h in NEWS2_FREQUENCY_H)
        elif r.window_h is None and "lactate" in r.params:
            win = "4 h if > 2 mmol/L, else 24 h"
        elif r.window_h is None and "hgb" in r.params:
            win = "6 h while falling, else 24 h"
        else:
            win = f"{r.window_h:g} h"
        refs = "<br>".join(f"PMID {p}" + (f" ([doi](https://doi.org/{d}))" if d else "")
                           for p, d in zip(r.pmids, list(r.dois) + [""] * len(r.pmids))) or "—"
        todo = (r.notes + "; " if r.notes else "") + f"query: `{r.pubmed_query}`"
        lines.append(f"| {', '.join(f'`{p}`' for p in r.params)} | {win} | {r.basis} | {r.source} | {refs} | "
                     f"{r.status} | {todo} |")
    lines.append("")
    counts = {st: sum(1 for r in RULES if r.status == st) for st in STATUSES}
    lines.append(f"{counts[VERIFIED]} of {len(RULES)} windows verified; {counts[CITATION_CONFIRMED]} citation "
                 f"confirmed; {counts[NOT_IN_PUBMED]} source not in PubMed; {counts[UNVERIFIED]} expert "
                 "placeholders.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render())
