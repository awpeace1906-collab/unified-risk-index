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
Stat Med 2016;35:214-26), and the calibration slope estimated with a 95% CI
no wider than 0.2. Riley 2021 found the slope criterion is usually the one
that binds. It needs the distribution of the model's linear predictor (LP):
until the Phase 1 model exists, LP ~ Normal(mu, sigma) is chosen so the model
is calibrated at the anticipated event rate and has the anticipated C, and
    var(slope) = I_a / (n * (I_a * I_b - I_ab^2)),
with I_a = E[p(1-p)], I_ab = E[LP p(1-p)], I_b = E[LP^2 p(1-p)], p = expit(LP).
Replace mu and sigma with the fitted model's LP mean and SD when available.
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


_GRID = [-6 + 12 * i / 400 for i in range(401)]  # standard-normal grid, +/- 6 SD


def _expit(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -700 else 0.0


def _lp_moments(mu: float, sigma: float) -> tuple[float, float]:
    """Event rate and C-statistic of a calibrated model with LP ~ N(mu, sigma)."""
    w = [math.exp(-z * z / 2) for z in _GRID]
    tw = sum(w)
    p = [_expit(mu + sigma * z) for z in _GRID]
    ev = [wi * pi / tw for wi, pi in zip(w, p)]
    ne = [wi * (1 - pi) / tw for wi, pi in zip(w, p)]
    phi = sum(ev)
    # C = P(LP_event > LP_non-event), ties counted half
    below, conc = 0.0, 0.0
    for e, n in zip(ev, ne):
        conc += e * (below + n / 2)
        below += n
    return phi, conc / (phi * (1 - phi))


def lp_distribution(phi: float, c: float) -> tuple[float, float]:
    """(mu, sigma) of a normal LP giving event rate ``phi`` and C-statistic ``c``."""
    if not 0.5 < c < 0.99:
        raise ValueError("anticipated C must be between 0.5 and 0.99")

    def mu_for(sigma):
        lo, hi = -30.0, 30.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _lp_moments(mid, sigma)[0] < phi else (lo, mid)
        return (lo + hi) / 2

    lo, hi = 0.01, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _lp_moments(mu_for(mid), mid)[1] < c else (lo, mid)
    sigma = (lo + hi) / 2
    return mu_for(sigma), sigma


def n_calibration_slope(mu: float, sigma: float, ci_width: float = 0.2) -> int:
    """Riley 2021 criterion: n giving a 95% CI of ``ci_width`` for the calibration
    slope (true slope 1) when the LP is Normal(mu, sigma)."""
    w = [math.exp(-z * z / 2) for z in _GRID]
    tw = sum(w)
    ia = iab = ib = 0.0
    for wi, z in zip(w, _GRID):
        lp = mu + sigma * z
        p = _expit(lp)
        v = wi * p * (1 - p) / tw
        ia, iab, ib = ia + v, iab + v * lp, ib + v * lp * lp
    se = ci_width / (2 * Z)
    return math.ceil(ia / (se ** 2 * (ia * ib - iab ** 2)))


def validation(phi: float, c: float = 0.75, oe_ci_width: float = 0.2, c_ci_width: float = 0.1,
               min_events: int = 100, slope_ci_width: float = 0.2,
               lp: tuple[float, float] | None = None) -> dict:
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
    mu, sigma = lp or lp_distribution(phi, c)
    n_slope = n_calibration_slope(mu, sigma, slope_ci_width)
    sizes = {"O/E": n_oe, "C": n_c, "events": n_events, "slope": n_slope}
    n = math.ceil(max(sizes.values()))
    return {
        "event_rate": phi, "anticipated_c": c, "lp_mean": round(mu, 3), "lp_sd": round(sigma, 3),
        "n_oe_precision": math.ceil(n_oe), "n_c_precision": n_c, "n_min_events": math.ceil(n_events),
        "n_calibration_slope": n_slope,
        "n": n, "events": math.ceil(n * phi), "binding": max(sizes, key=sizes.get),
        "note": ("LP distribution from the Phase 1 model" if lp else
                 "LP assumed normal and matched to the event rate and C; use the Phase 1 model's LP once fitted"),
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
    p.add_argument("--lp", type=float, nargs=2, metavar=("MEAN", "SD"),
                   help="linear-predictor mean and SD of the Phase 1 model (default: derived from rate and C)")
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
    v = validation(args.event_rate, args.c, args.oe_ci_width, lp=tuple(args.lp) if args.lp else None)
    print(f"\nPhase 2/3 external validation (Riley 2021; anticipated C = {args.c}, "
          f"LP ~ N({v['lp_mean']}, {v['lp_sd']}))")
    print(f"  O/E precision: n = {v['n_oe_precision']}   C precision: n = {v['n_c_precision']}   "
          f">= 100 events: n = {v['n_min_events']}   calibration slope: n = {v['n_calibration_slope']}")
    print(f"  minimum n = {v['n']} ({v['events']} events), set by {v['binding']}. {v['note']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
