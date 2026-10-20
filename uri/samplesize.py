"""Sample-size calculations for the Phase 1 derivation and Phase 2/3 validation.

    python -m uri.samplesize --event-rate 0.06 [--parameters 4 14] [--r2 0.10 0.15 0.20]

Development (Riley RD et al. Stat Med 2019;38:1276-96, Part II; worked
examples in Riley et al. BMJ 2020;368:m441). The minimum n satisfies all of:
  (i)   expected uniform shrinkage S >= 0.9:
            n = P / ((S - 1) * ln(1 - R2cs / S))
  (ii)  apparent vs adjusted Nagelkerke R2 differ by <= 0.05:
            S = R2cs / (R2cs + 0.05 * maxR2cs), then the same formula
  (iii) overall risk estimated within +/- 0.05:
            n = (1.96 / 0.05)^2 * phi * (1 - phi)
where maxR2cs = 1 - exp(2 * (phi ln phi + (1 - phi) ln(1 - phi))).
P counts candidate predictor parameters, not just the predictors kept.

External validation (Riley RD et al. Stat Med 2021;40:4230-51, binary
outcomes): precision of observed/expected (O/E),
    n = (1 - phi) / (phi * SE(ln O/E)^2),
precision of the C-statistic (Hanley & McNeil 1982 variance), and the
widely used floor of >= 100 events, preferably >= 200 (Collins GS et al.
Stat Med 2016;35:214-26). Riley 2021's calibration-slope criterion needs the
anticipated distribution of the linear predictor; take it from the Phase 1
model once it exists.
"""

from __future__ import annotations

import argparse
import math

Z = 1.96


def max_r2cs(phi: float) -> float:
    ln_null = phi * math.log(phi) + (1 - phi) * math.log(1 - phi)
    return 1 - math.exp(2 * ln_null)


def _n_for_shrinkage(p: int, r2: float, s: float) -> float:
    return p / ((s - 1) * math.log(1 - r2 / s))


def development(phi: float, parameters: int, r2cs: float, shrinkage: float = 0.9,
                r2_margin: float = 0.05, risk_margin: float = 0.05) -> dict:
    """Riley 2019 Part II minimum sample size for a binary-outcome model."""
    if not 0 < phi < 1:
        raise ValueError("event rate must be between 0 and 1")
    mx = max_r2cs(phi)
    if not 0 < r2cs < mx:
        raise ValueError(f"R2cs must be between 0 and its maximum {mx:.3f} at this event rate")
    n1 = _n_for_shrinkage(parameters, r2cs, shrinkage)
    s2 = r2cs / (r2cs + r2_margin * mx)
    n2 = _n_for_shrinkage(parameters, r2cs, s2)
    n3 = (Z / risk_margin) ** 2 * phi * (1 - phi)
    n = math.ceil(max(n1, n2, n3))
    return {
        "event_rate": phi, "parameters": parameters, "r2cs": r2cs, "max_r2cs": round(mx, 4),
        "n_criterion_i": math.ceil(n1), "n_criterion_ii": math.ceil(n2), "n_criterion_iii": math.ceil(n3),
        "shrinkage_criterion_ii": round(s2, 3),
        "n": n, "events": math.ceil(n * phi), "events_per_parameter": round(n * phi / parameters, 1),
        "binding": ["i", "ii", "iii"][[n1, n2, n3].index(max(n1, n2, n3))],
    }


def _c_se(c: float, events: float, non_events: float) -> float:
    q1, q2 = c / (2 - c), 2 * c * c / (1 + c)
    var = (c * (1 - c) + (events - 1) * (q1 - c * c) + (non_events - 1) * (q2 - c * c)) / (events * non_events)
    return math.sqrt(var)


def validation(phi: float, c: float = 0.75, oe_ci_width: float = 0.2, c_ci_width: float = 0.1,
               min_events: int = 100) -> dict:
    """Minimum external-validation sample size for a binary outcome."""
    # O/E: a 95% CI of about +/- width/2 around O/E = 1 on the log scale
    se_ln_oe = math.log(1 + oe_ci_width / 2) / Z
    n_oe = (1 - phi) / (phi * se_ln_oe ** 2)
    # C-statistic: smallest n whose 95% CI width is <= c_ci_width
    target_se = c_ci_width / (2 * Z)
    n_c = 10
    while _c_se(c, n_c * phi, n_c * (1 - phi)) > target_se:
        n_c = math.ceil(n_c * 1.02) + 1
    n_events = min_events / phi
    n = math.ceil(max(n_oe, n_c, n_events))
    return {
        "event_rate": phi, "anticipated_c": c,
        "n_oe_precision": math.ceil(n_oe), "n_c_precision": n_c, "n_min_events": math.ceil(n_events),
        "n": n, "events": math.ceil(n * phi),
        "note": "calibration-slope criterion (Riley 2021) needs the Phase 1 linear-predictor distribution",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uri.samplesize", description="Riley-criteria sample sizes for URI")
    p.add_argument("--event-rate", type=float, required=True, help="from the Phase 0 audit, e.g. 0.06")
    p.add_argument("--parameters", type=int, nargs="+", default=[4, 14],
                   help="candidate parameters: 4 = URI v1 components, 14 = URI v2 model features")
    p.add_argument("--r2", type=float, nargs="+", default=[0.10, 0.15, 0.20],
                   help="anticipated Cox-Snell R2 (proposal: 0.10-0.20)")
    p.add_argument("--r2-fraction", type=float, default=0.15,
                   help="also report R2cs = this fraction of its maximum (Riley 2020's conservative "
                        "default when no prior model is available); 0 to skip")
    p.add_argument("--c", type=float, default=0.75, help="anticipated C-statistic for validation")
    p.add_argument("--oe-ci-width", type=float, default=0.2, help="target 95%% CI width for O/E")
    args = p.parse_args(argv)
    if args.r2_fraction:
        args.r2 = list(args.r2) + [round(args.r2_fraction * max_r2cs(args.event_rate), 4)]
    print(f"Event rate {args.event_rate:.1%}; max Cox-Snell R2 at this rate {max_r2cs(args.event_rate):.3f}\n")
    print("Phase 1 derivation (Riley 2019 Part II)")
    print(f"{'params':>6} {'R2cs':>5} {'n(i)':>7} {'n(ii)':>7} {'n(iii)':>7} {'min n':>7} {'events':>7} {'EPP':>5}")
    for params in args.parameters:
        for r2 in args.r2:
            try:
                d = development(args.event_rate, params, r2)
            except ValueError as exc:
                print(f"{params:>6} {r2:>5.3f}  {exc}")
                continue
            print(f"{params:>6} {r2:>5.3f} {d['n_criterion_i']:>7} {d['n_criterion_ii']:>7} "
                  f"{d['n_criterion_iii']:>7} {d['n']:>7} {d['events']:>7} {d['events_per_parameter']:>5}")
    if args.r2_fraction:
        print(f"(last R2cs row = {args.r2_fraction:.0%} of max, the conservative choice)")
    v = validation(args.event_rate, args.c, args.oe_ci_width)
    print(f"\nPhase 2/3 external validation (anticipated C = {args.c})")
    print(f"  O/E precision: n = {v['n_oe_precision']}   C precision: n = {v['n_c_precision']}   "
          f">= 100 events: n = {v['n_min_events']}")
    print(f"  minimum n = {v['n']} ({v['events']} events). {v['note']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
