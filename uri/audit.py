"""Phase 0 event-rate audit (research proposal section 0.1).

    python -m uri.audit audit.csv [--min-rate 0.03]
    python -m uri.audit --engine-outcomes outcomes.csv --index index.csv

Input is the chart-abstraction sheet (templates/phase0_audit.csv): one row
per consecutive adult ED admission, the triage time, and the time of each
composite component (blank = did not occur). Alternatively, outcomes written
by `python -m uri replay --outcomes-out` plus an index CSV
(patient_id,triage_time) from the same export.

Reports, per window, the composite event rate with a Wilson 95% CI
(cumulative: within 6/12/24/36/48 h; and by interval: 0-6, 6-12, ... h), the
component breakdown, and the proposal's decision rule. Admissions are
assumed followed for 48 h in hospital: a patient discharged without an event
counts as a non-event.

The audit sheet holds PHI. Keep it outside this repository (data/ is
git-ignored) and run the tool locally.
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import timedelta

from .events import OUTCOME_TYPES, parse_time
from .model import HORIZONS_H
from .samplesize import development, max_r2cs, validation

COMPONENTS = ("icu_transfer", "rrt", "intubation", "vasopressor_start", "death")
TIME_COLUMNS = {c: f"{c}_time" for c in COMPONENTS}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_audit(path: str) -> tuple[list[dict], list[dict]]:
    """Rows from the abstraction sheet -> (included, excluded)."""
    included, excluded = [], []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("triage_time") or "").strip():
                continue
            if (row.get("excluded_reason") or "").strip():
                excluded.append(row)
                continue
            if (row.get("admitted") or "Y").strip().upper() not in ("Y", "YES", "1", "TRUE"):
                excluded.append({**row, "excluded_reason": "not admitted"})
                continue
            rec = {"id": row.get("record_id", ""), "triage": parse_time(row["triage_time"]),
                   "events": {}, "revisit_72h": (row.get("ed_revisit_72h") or "").strip().upper()
                   in ("Y", "YES", "1", "TRUE")}
            for comp, col in TIME_COLUMNS.items():
                if (row.get(col) or "").strip():
                    rec["events"][comp] = parse_time(row[col])
            included.append(rec)
    return included, excluded


def from_engine(outcomes_path: str, index_path: str) -> list[dict]:
    with open(index_path, newline="") as fh:
        recs = {r["patient_id"]: {"id": r["patient_id"], "triage": parse_time(r["triage_time"]),
                                  "events": {}, "revisit_72h": False} for r in csv.DictReader(fh)}
    with open(outcomes_path, newline="") as fh:
        for r in csv.DictReader(fh):
            rec = recs.get(r["patient_id"])
            kind = r.get("type")
            if rec is None or kind not in OUTCOME_TYPES:
                continue
            t = parse_time(r["time"])
            if kind not in rec["events"] or t < rec["events"][kind]:
                rec["events"][kind] = t
    return list(recs.values())


def first_event_hours(rec: dict) -> float | None:
    """Hours from triage to the first composite component (events before triage ignored)."""
    deltas = [(t - rec["triage"]) / timedelta(hours=1) for t in rec["events"].values() if t >= rec["triage"]]
    return min(deltas) if deltas else None


def summarize(records: list[dict]) -> dict:
    n = len(records)
    first = [first_event_hours(r) for r in records]
    cumulative, interval = [], []
    lo = 0
    for h in HORIZONS_H:
        k = sum(1 for x in first if x is not None and x <= h)
        cumulative.append({"window": f"0-{h}h", "events": k, "rate": k / n if n else float("nan"),
                           "ci95": wilson(k, n)})
        ki = sum(1 for x in first if x is not None and (lo < x <= h or (lo == 0 and x == 0)))
        interval.append({"window": f"{lo}-{h}h", "events": ki, "rate": ki / n if n else float("nan")})
        lo = h
    comps = {}
    for comp in COMPONENTS:
        k = sum(1 for r in records if comp in r["events"]
                and timedelta(0) <= r["events"][comp] - r["triage"] <= timedelta(hours=48))
        comps[comp] = {"events": k, "rate": k / n if n else float("nan")}
    revisits = sum(1 for r in records if r["revisit_72h"])
    return {"n": n, "cumulative": cumulative, "interval": interval, "components": comps,
            "revisit_72h": revisits}


def decision(rate48: float, min_rate: float = 0.03) -> str:
    """The proposal's Phase 0 decision rules (sections 1.5 KEY INSIGHT and 5.1)."""
    if rate48 < min_rate:
        return ("48 h composite rate below {:.0%}: per the proposal, reframe Phase 1 around the 48 h window "
                "only with the composite broadened to include ED revisit within 72 h, plan Phase 2 as "
                "multicenter from the start, and treat the Phase 4 RCT as likely underpowered.").format(min_rate)
    return ("48 h composite rate at or above {:.0%}: proceed with the single-center Phase 1 retrospective "
            "derivation as planned.").format(min_rate)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uri.audit", description="Phase 0 composite event-rate audit")
    p.add_argument("audit_csv", nargs="?", help="chart-abstraction sheet (templates/phase0_audit.csv)")
    p.add_argument("--engine-outcomes", help="outcomes CSV from `uri replay --outcomes-out`")
    p.add_argument("--index", help="patient_id,triage_time CSV (with --engine-outcomes)")
    p.add_argument("--min-rate", type=float, default=0.03)
    args = p.parse_args(argv)
    if args.engine_outcomes:
        if not args.index:
            p.error("--engine-outcomes needs --index")
        records, excluded = from_engine(args.engine_outcomes, args.index), []
    elif args.audit_csv:
        records, excluded = load_audit(args.audit_csv)
    else:
        p.error("give an audit CSV or --engine-outcomes/--index")
    s = summarize(records)
    if not s["n"]:
        print("no eligible admissions")
        return 1
    print(f"Eligible admissions: {s['n']}   excluded: {len(excluded)}\n")
    print("Composite decompensation (ICU transfer, RRT, intubation, vasopressor, death), first event")
    print(f"{'window':>8} {'events':>7} {'rate':>7}   95% CI (Wilson)   | {'interval':>8} {'events':>7}")
    for c, i in zip(s["cumulative"], s["interval"]):
        lo, hi = c["ci95"]
        print(f"{c['window']:>8} {c['events']:>7} {c['rate']:>7.1%}   {lo:>6.1%} - {hi:<6.1%}     "
              f"| {i['window']:>8} {i['events']:>7}")
    print("\nComponents within 48 h (a patient can have several):")
    for comp, d in s["components"].items():
        print(f"  {comp:<18} {d['events']:>4}  {d['rate']:.1%}")
    if s["revisit_72h"]:
        print(f"  ED revisit <= 72 h  {s['revisit_72h']:>4}  {s['revisit_72h'] / s['n']:.1%}")
    rate48 = s["cumulative"][-1]["rate"]
    print(f"\nDecision: {decision(rate48, args.min_rate)}")
    if 0 < rate48 < 1:
        conservative = 0.15 * max_r2cs(rate48)
        print(f"\nPhase 1 sample sizes at the observed 48 h rate ({rate48:.1%}); anticipated Cox-Snell R2 = "
              f"proposal's 0.15 / conservative 15% of max ({conservative:.3f}):")
        for params, label in ((4, "URI v1 (4 component scores)"), (14, "URI v2 model (14 features)")):
            sizes = []
            for r2 in (min(0.15, 0.9 * max_r2cs(rate48)), conservative):
                d = development(rate48, params, r2)
                sizes.append(f"n = {d['n']} ({d['events']} events)")
            print(f"  {label}: {sizes[0]}  /  {sizes[1]}")
        v = validation(rate48)
        print(f"  Phase 2 validation: n = {v['n']} ({v['events']} events); see `python -m uri.samplesize`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
