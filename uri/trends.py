"""Trajectory layer: risk carried by *change over time*, not the current value.

A single snapshot cannot see a hemoglobin falling from 12 to 9.5, a lactate
that is not clearing, or a NEWS2 that has crept up 3 points since arrival.
Each detector below looks back over the patient's own series and returns
points plus a human-readable reason. Thresholds are clinically conventional
starting points (KDIGO creatinine criteria, Sepsis-3 delta-SOFA, ROX);
all are recalibration targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config
from .series import Series

H = timedelta(hours=1)


@dataclass
class Signal:
    id: str
    points: float
    detail: str

    def to_dict(self) -> dict:
        return {"id": self.id, "points": self.points, "detail": self.detail}


def _num(series: Series | None, at: datetime, span: timedelta) -> list[tuple[datetime, float]]:
    if series is None:
        return []
    return [(t, x) for t, x in series.window(at - span, at) if isinstance(x, (int, float))]


def component_rise(hist: Series, at: datetime, span: timedelta) -> float | None:
    """Rise of a points-based score over its minimum in ``span``, summed per
    component and counting only components that were measured before. A lab
    resulting for the first time is new information, not deterioration."""
    pts = hist.window(at - span, at) if hist else []
    if len(pts) < 2:
        return None
    rise = 0.0
    for name, score in pts[-1][1].items():
        prior = [p[name] for _, p in pts[:-1] if name in p]
        if prior:
            rise += score - min(prior)
    return rise


def news2_rise(news2_hist: Series, at: datetime) -> Signal | None:
    rise = component_rise(news2_hist, at, 6 * H)
    if rise is not None and rise >= 4:
        return Signal("news2_rise", 3, f"NEWS2 up {rise:g} points in 6 h")
    if rise is not None and rise >= 2:
        return Signal("news2_rise", 2, f"NEWS2 up {rise:g} points in 6 h")
    return None


def shock_index_trend(hr: Series, sbp: Series, at: datetime, threshold: float) -> Signal | None:
    hrs, sbps = _num(hr, at, 4 * H), _num(sbp, at, 4 * H)
    if not hrs or not sbps or not sbps[-1][1]:
        return None
    si = hrs[-1][1] / sbps[-1][1]
    points, detail = 0, []
    if si >= threshold:
        points += 2
        detail.append(f"shock index {si:.2f} (>= {threshold:g})")
    if len(hrs) >= 2 and len(sbps) >= 2 and sbps[0][1]:
        si0 = hrs[0][1] / sbps[0][1]
        if si - si0 >= 0.2:
            points += 1
            detail.append(f"rising from {si0:.2f}")
    return Signal("shock_index", points, ", ".join(detail)) if points else None


def map_sustained_low(map_values: list[tuple[datetime, float]]) -> Signal | None:
    """MAP < 65 on the last two readings spanning at least 15 minutes."""
    if len(map_values) < 2:
        return None
    (t1, m1), (t2, m2) = map_values[-2], map_values[-1]
    if m1 < 65 and m2 < 65 and t2 - t1 >= timedelta(minutes=15):
        return Signal("map_low_sustained", 2, f"MAP < 65 sustained ({m1:.0f} -> {m2:.0f})")
    return None


def lactate_trend(lactate: Series, at: datetime) -> Signal | None:
    pts = _num(lactate, at, 12 * H)
    if len(pts) < 2:
        return None
    first, last = pts[0][1], pts[-1][1]
    if last > first and last >= 2:
        return Signal("lactate_rising", 2, f"lactate rising {first:g} -> {last:g} mmol/L")
    if first >= 2 and (first - last) / first < 0.10:
        return Signal("lactate_not_clearing", 1, f"lactate clearance < 10% ({first:g} -> {last:g})")
    return None


def hgb_drop(hgb: Series, at: datetime, baseline: float | None = None) -> Signal | None:
    """Fall within 36 h (wide enough to compare q24h ICU draws), or (with a
    pre-episode baseline) below the patient's usual value, which catches
    bleeding already under way at arrival."""
    pts = _num(hgb, at, 36 * H)
    if not pts:
        return None
    best = None
    if len(pts) >= 2:
        drop = max(x for _, x in pts) - pts[-1][1]
        if drop >= 1:
            best = Signal("hgb_drop", 3 if drop >= 2 else 1, f"Hgb down {drop:.1f} g/dL in 36 h")
    if baseline is not None:
        below = baseline - pts[-1][1]
        if below >= 2 and (best is None or best.points < (3 if below >= 3 else 2)):
            best = Signal("hgb_drop", 3 if below >= 3 else 2,
                          f"Hgb {pts[-1][1]:g}, {below:.1f} g/dL below baseline {baseline:g}")
    return best


def aki_kdigo(creatinine: Series, at: datetime, baseline: float | None = None) -> Signal | None:
    """KDIGO 2012. With a pre-episode baseline the stage is graded on arrival
    from a single value; without one, the 7-day minimum stands in."""
    pts48 = _num(creatinine, at, 48 * H)
    if not pts48:
        return None
    last = pts48[-1][1]
    rise48 = len(pts48) >= 2 and last - min(x for _, x in pts48[:-1]) >= 0.3
    if baseline:
        ratio = last / baseline
        stage = 3 if ratio >= 3 or (last >= 4.0 and (rise48 or ratio >= 1.5)) else \
            2 if ratio >= 2 else 1 if ratio >= 1.5 or rise48 else 0
        if stage:
            return Signal("aki", 2 if stage == 1 else 3,
                          f"KDIGO stage {stage}: creatinine {last:g} = {ratio:.1f}x baseline {baseline:g}")
        return None
    pts7d = _num(creatinine, at, 7 * 24 * H)
    if len(pts7d) < 2:
        return None
    if rise48:
        return Signal("aki", 2, "creatinine up >= 0.3 mg/dL in 48 h (KDIGO)")
    base = min(x for _, x in pts7d[:-1])
    if base and last / base >= 1.5:
        return Signal("aki", 2, f"creatinine {last / base:.1f}x 7-day minimum (KDIGO; no prior baseline)")
    return None


def troponin_delta(trop: Series, at: datetime, url: float) -> Signal | None:
    pts = _num(trop, at, 12 * H)
    if len(pts) < 2:
        return None
    first, last = pts[0][1], pts[-1][1]
    if last > url and first and (last - first) / first >= 0.20:
        return Signal("troponin_rising", 2, f"hs-troponin rising {first:g} -> {last:g} ng/L")
    return None


def oxygenation_decline(spo2: Series, o2: Series, fio2: Series, at: datetime) -> Signal | None:
    detail = []
    pts = _num(spo2, at, 2 * H)
    if len(pts) >= 2:
        drop = max(x for _, x in pts) - pts[-1][1]
        if drop >= 4:
            detail.append(f"SpO2 down {drop:g} points in 2 h")
    on = o2.window(at - 4 * H, at) if o2 else []
    if len(on) >= 2 and on[-1][1] and not on[0][1]:
        detail.append("new supplemental O2")
    f = _num(fio2, at, 4 * H)
    if len(f) >= 2 and f[-1][1] - f[0][1] >= 10:
        detail.append(f"FiO2 escalated {f[0][1]:g} -> {f[-1][1]:g}%")
    return Signal("oxygenation_decline", 2, "; ".join(detail)) if detail else None


def sofa_rise(sofa_hist: Series, at: datetime) -> Signal | None:
    """Acute rise of >= 2 SOFA points over the 48 h minimum: the Sepsis-3
    operational definition of new organ dysfunction."""
    rise = component_rise(sofa_hist, at, 48 * H)
    if rise is not None and rise >= 2:
        return Signal("sofa_rise", 3, f"SOFA up {rise:g} points (>= 2: new organ dysfunction)")
    return None


def rox_low(v: dict) -> Signal | None:
    """ROX = (SpO2 / FiO2) / RR, for patients on charted FiO2 (HFNC / NIV).
    < 3.85 predicts high-flow failure (Roca 2019)."""
    fio2, spo2, rr = v.get("fio2"), v.get("spo2"), v.get("rr")
    if fio2 is None or fio2 <= 21 or spo2 is None or not rr:
        return None
    rox = spo2 / (fio2 / 100) / rr
    if rox < 3.85:
        return Signal("rox_low", 2, f"ROX {rox:.2f} (< 3.85)")
    return None


def rr_rise(rr: Series, at: datetime) -> Signal | None:
    pts = _num(rr, at, 4 * H)
    if len(pts) >= 2 and pts[-1][1] - min(x for _, x in pts[:-1]) >= 6:
        return Signal("rr_rise", 1, f"RR up {pts[-1][1] - min(x for _, x in pts[:-1]):g}/min in 4 h")
    return None


def gcs_drop(gcs: Series, at: datetime) -> Signal | None:
    pts = _num(gcs, at, 6 * H)
    if len(pts) >= 2 and max(x for _, x in pts) - pts[-1][1] >= 2:
        return Signal("gcs_drop", 2, f"GCS down {max(x for _, x in pts) - pts[-1][1]:g} in 6 h")
    return None


def support_escalation(support: Series, at: datetime) -> Signal | None:
    """Step up the respiratory-support ladder (room air -> low-flow -> HFNC ->
    NIV -> invasive) within 6 h."""
    pts = _num(support, at, 6 * H)
    if len(pts) < 2:
        return None
    low, now = min(x for _, x in pts[:-1]), pts[-1][1]
    if now - low >= 2 or (now > low and now >= 3):
        return Signal("resp_support_escalation", 3, f"respiratory support escalated {low:g} -> {now:g} in 6 h")
    if now > low:
        return Signal("resp_support_escalation", 2, f"respiratory support escalated {low:g} -> {now:g} in 6 h")
    return None


def pressor_escalation(pressors: Series, at: datetime) -> Signal | None:
    pts = _num(pressors, at, 6 * H)
    if len(pts) >= 2 and pts[-1][1] > min(x for _, x in pts[:-1]) and pts[-1][1] >= 2:
        return Signal("pressor_escalation", 3, f"{pts[-1][1]:g} concurrent vasopressors (added within 6 h)")
    return None


def trajectory(series: dict[str, Series], news2_hist: Series, sofa_hist: Series, map_values, v: dict,
               at: datetime, cfg: Config, pressor_hist: Series | None = None, baseline: dict | None = None,
               sofa_above_baseline: int | None = None) -> tuple[float, list[Signal]]:
    g = series.get
    base = baseline or {}

    def bval(param):
        return (base.get(param) or {}).get("value")

    sofa_signal = sofa_rise(sofa_hist, at)
    if sofa_signal is None and sofa_above_baseline is not None and sofa_above_baseline >= 2:
        sofa_signal = Signal("sofa_rise", 3, f"SOFA {sofa_above_baseline:g} points above pre-episode baseline "
                                             "(>= 2: new organ dysfunction)")
    signals = [
        news2_rise(news2_hist, at),
        sofa_signal,
        rox_low(v),
        shock_index_trend(g("hr"), g("sbp"), at, cfg.shock_index_threshold),
        map_sustained_low(map_values),
        lactate_trend(g("lactate"), at),
        hgb_drop(g("hgb"), at, bval("hgb")),
        aki_kdigo(g("creatinine"), at, bval("creatinine")),
        troponin_delta(g("troponin_hs"), at, cfg.troponin_url_ng_l),
        oxygenation_decline(g("spo2"), g("o2_supplemental"), g("fio2"), at),
        rr_rise(g("rr"), at),
        gcs_drop(g("gcs"), at),
        support_escalation(g("resp_support"), at),
        pressor_escalation(pressor_hist, at),
    ]
    signals = [s for s in signals if s]
    return sum(s.points for s in signals), signals
