"""Fit the risk-window model from logged snapshots and observed outcomes.

    python -m uri.calibrate --snapshots shadow.jsonl --outcomes outcomes.csv \
        --out model.json [--bootstrap 200] [--landmark-min 60] [--encounters enc.csv]

snapshots   JSONL written by `--log` (replay of a historical export, or shadow mode)
outcomes    CSV: patient_id,time[,type]. The first composite event per patient
            (unplanned ICU transfer, RRT, emergent intubation, vasopressor
            start, death) is used.
encounters  optional CSV: patient_id,end_time. Rows whose follow-up ends
            before the horizon without an event are dropped (censoring).
            Without it every patient is assumed followed for 48 h.

One L2-penalized logistic model is fitted per horizon (6/12/24/36/48 h,
cumulative). Patients contribute one landmark snapshot per --landmark-min
interval, taken only before their first event. The report gives apparent and
bootstrap optimism-corrected AUROC, calibration slope (shrinkage), Brier
score, and AUROC of NEWS2 alone, URI v1 and the uncalibrated URI v2 at the
same landmarks, i.e. the comparisons the Phase 1 TRIPOD report needs.

Requires numpy (the streaming engine itself does not).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import timedelta

import numpy as np

from .events import parse_time
from .model import FEATURES, HORIZONS_H, features


# --- data assembly -------------------------------------------------------------

def load_outcomes(path: str) -> dict:
    first = {}
    with open(path) as fh:
        for row in csv.DictReader(fh):
            t = parse_time(row["time"])
            pid = row["patient_id"]
            if pid not in first or t < first[pid]:
                first[pid] = t
    return first


def load_ends(path: str | None) -> dict:
    if not path:
        return {}
    with open(path) as fh:
        return {r["patient_id"]: parse_time(r["end_time"]) for r in csv.DictReader(fh)}


def landmarks(snapshots, outcomes: dict, every: timedelta) -> list[dict]:
    """One snapshot per patient per interval, before the patient's first event."""
    by_patient = defaultdict(list)
    for snap in snapshots:
        by_patient[snap["patient_id"]].append(snap)
    rows = []
    for pid, snaps in by_patient.items():
        snaps.sort(key=lambda s: s["time"])
        event = outcomes.get(pid)
        last = None
        for snap in snaps:
            t = parse_time(snap["time"])
            if event is not None and t >= event:
                break
            # take the latest snapshot in each interval (it has the most data)
            if last is not None and t - last["_t"] < every:
                rows[-1] = {**snap, "_t": last["_t"], "_time": t}
                continue
            last = {**snap, "_t": t, "_time": t}
            rows.append(last)
    return rows


def design(rows: list[dict], outcomes: dict, ends: dict, horizon_h: int):
    h = timedelta(hours=horizon_h)
    X, y, groups, comparators = [], [], [], []
    for r in rows:
        pid, t = r["patient_id"], r["_time"]
        event = outcomes.get(pid)
        hit = event is not None and t < event <= t + h
        if not hit and pid in ends and ends[pid] < t + h:
            continue  # censored before the horizon
        f = features(r)
        X.append([f[k] for k in FEATURES])
        y.append(1.0 if hit else 0.0)
        groups.append(pid)
        comparators.append((r["scores"]["news2"]["total"], r["uri_v1"], r["uri"]))
    return np.array(X, float), np.array(y), np.array(groups), np.array(comparators, float)


# --- statistics ----------------------------------------------------------------

def fit_logistic(X, y, l2: float = 1.0, iters: int = 50):
    """Newton-Raphson for L2-penalized logistic regression (intercept unpenalized)."""
    n, k = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(k + 1)
    penalty = np.eye(k + 1) * l2
    penalty[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(A @ beta)))
        w = p * (1 - p)
        grad = A.T @ (y - p) - penalty @ beta
        hess = (A * w[:, None]).T @ A + penalty
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def predict(beta, X):
    return 1 / (1 + np.exp(-(beta[0] + X @ beta[1:])))


def auroc(score, y) -> float:
    """Mann-Whitney AUROC with average ranks for ties."""
    pos, neg = y == 1, y == 0
    if not pos.any() or not neg.any():
        return float("nan")
    values, inverse, counts = np.unique(score, return_inverse=True, return_counts=True)
    upper = np.cumsum(counts)
    avg_rank = upper - (counts - 1) / 2
    ranks = avg_rank[inverse]
    n_pos, n_neg = pos.sum(), neg.sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def calibration_slope(p, y) -> float:
    lp = np.log(np.clip(p, 1e-9, 1 - 1e-9) / np.clip(1 - p, 1e-9, 1))
    return float(fit_logistic(lp[:, None], y, l2=0.0)[1])


def fit_horizon(X, y, groups, comparators, l2: float, n_boot: int, rng) -> dict:
    mean, scale = X.mean(0), X.std(0)
    scale[scale == 0] = 1
    Z = (X - mean) / scale
    beta = fit_logistic(Z, y, l2)
    p = predict(beta, Z)
    apparent = auroc(p, y)
    result = {
        "intercept": float(beta[0]), "coef": [float(b) for b in beta[1:]],
        "n_rows": int(len(y)), "n_patients": int(len(set(groups))), "events": int(y.sum()),
        "auroc_apparent": apparent, "brier": float(np.mean((p - y) ** 2)),
        "comparators_auroc": {"news2": auroc(comparators[:, 0], y), "uri_v1": auroc(comparators[:, 1], y),
                              "uri_v2_uncalibrated": auroc(comparators[:, 2], y)},
    }
    if n_boot:
        # Harrell's bootstrap, resampling patients (rows within a patient are correlated).
        patients = np.unique(groups)
        index = {g: np.flatnonzero(groups == g) for g in patients}
        optimism, slopes = [], []
        for _ in range(n_boot):
            rows = np.concatenate([index[g] for g in rng.choice(patients, len(patients))])
            if y[rows].min() == y[rows].max():
                continue
            b = fit_logistic(Z[rows], y[rows], l2)
            optimism.append(auroc(predict(b, Z[rows]), y[rows]) - auroc(predict(b, Z), y))
            slopes.append(calibration_slope(predict(b, Z), y))
        result["auroc_optimism"] = float(np.mean(optimism))
        result["auroc_corrected"] = apparent - result["auroc_optimism"]
        result["calibration_slope"] = float(np.mean(slopes))  # target >= 0.9 (Riley)
    return result, mean, scale


def calibrate(snapshots, outcomes: dict, ends: dict | None = None, landmark_min: int = 60,
              l2: float = 1.0, n_boot: int = 0, seed: int = 0) -> dict:
    rows = landmarks(snapshots, outcomes, timedelta(minutes=landmark_min))
    rng = np.random.default_rng(seed)
    spec = {"version": 1, "features": list(FEATURES), "horizons": {}, "landmark_min": landmark_min, "l2": l2}
    # Standardize on the 48 h design (all rows) so every horizon shares one scaling.
    X48, *_ = design(rows, outcomes, ends or {}, 48)
    if not len(X48):
        raise ValueError("no landmark rows: check that snapshots and outcomes share patient_ids")
    for h in HORIZONS_H:
        X, y, groups, comp = design(rows, outcomes, ends or {}, h)
        if y.min() == y.max():
            raise ValueError(f"{h}h horizon has only one outcome class; more data needed")
        res, _, _ = fit_horizon(X, y, groups, comp, l2, n_boot, rng)
        spec["horizons"][f"{h}h"] = res
    mean, scale = X48.mean(0), X48.std(0)
    scale[scale == 0] = 1
    # Refit with the shared scaling so stored coefficients match "mean"/"scale".
    for h in HORIZONS_H:
        X, y, *_ = design(rows, outcomes, ends or {}, h)
        beta = fit_logistic((X - mean) / scale, y, l2)
        spec["horizons"][f"{h}h"].update(intercept=float(beta[0]), coef=[float(b) for b in beta[1:]])
    spec["mean"], spec["scale"] = mean.tolist(), scale.tolist()
    return spec


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uri.calibrate", description=__doc__.split("\n\n")[0])
    p.add_argument("--snapshots", required=True)
    p.add_argument("--outcomes", required=True)
    p.add_argument("--encounters")
    p.add_argument("--out", required=True)
    p.add_argument("--landmark-min", type=int, default=60)
    p.add_argument("--l2", type=float, default=1.0)
    p.add_argument("--bootstrap", type=int, default=0)
    args = p.parse_args(argv)
    with open(args.snapshots) as fh:
        snaps = [json.loads(line) for line in fh if line.strip()]
    spec = calibrate(snaps, load_outcomes(args.outcomes), load_ends(args.encounters),
                     args.landmark_min, args.l2, args.bootstrap)
    with open(args.out, "w") as fh:
        json.dump(spec, fh, indent=2)
    for h, r in spec["horizons"].items():
        c = r["comparators_auroc"]
        extra = f" corrected {r['auroc_corrected']:.3f} slope {r['calibration_slope']:.2f}" if "auroc_corrected" in r else ""
        print(f"{h:>4}: n={r['n_rows']} events={r['events']} AUROC {r['auroc_apparent']:.3f}{extra} "
              f"| NEWS2 {c['news2']:.3f} v1 {c['uri_v1']:.3f} v2-uncal {c['uri_v2_uncalibrated']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
