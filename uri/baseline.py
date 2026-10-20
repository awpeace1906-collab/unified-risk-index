"""Pre-episode baselines from the patient's prior results.

Organ dysfunction and acute kidney injury are defined as change from the
patient's own baseline (Sepsis-3; KDIGO 2012). Without prior results, a
patient with CKD at their usual creatinine of 2.2 looks like new renal
failure, and an Hgb of 9 looks the same in a patient whose usual value is 13
and one whose usual value is 9.

Episode. The current episode starts at the earliest measurement reached by
walking back from the latest one without crossing a gap longer than
``episode_gap_hours`` (36 h). The gap is measured across all of the
patient's results together: a lab not repeated for 72 h does not split the
visit while other results keep arriving. Results from earlier encounters
(pulled at arrival by uri.history, or included in a replayed export) fall
before that and form the baseline.

Baselines (looking back ``baseline_lookback_days`` from the episode start):
  creatinine   lowest value in the 7 days before the episode, else the median
               of days 8-365 (the KDIGO "within 7 days" reference with the
               commonly used outpatient-median fallback)
  hemoglobin, platelets, bilirubin
               median of the prior values
  SOFA         coagulation / liver / renal points of those baselines;
               respiratory, cardiovascular and CNS baselines are taken as 0
"""

from __future__ import annotations

import bisect
import statistics
from datetime import datetime, timedelta

from . import scores
from .series import Series

DAY = timedelta(days=1)


def episode_start(times: list[datetime], at: datetime, gap: timedelta) -> datetime | None:
    """``times`` sorted ascending; start of the run of measurements ending at ``at``."""
    i = bisect.bisect_right(times, at) - 1
    if i < 0:
        return None
    while i > 0 and times[i] - times[i - 1] <= gap:
        i -= 1
    return times[i]


def _values(series: Series | None, start: datetime, end: datetime) -> list[float]:
    """Numeric values with start <= t < end."""
    if series is None:
        return []
    return [x for t, x in series.window(start, end) if t < end and isinstance(x, (int, float))]


def compute(series: dict[str, Series], start: datetime | None, lookback_days: int = 365) -> dict:
    out: dict = {"episode_start": start.isoformat() if start else None}
    if start is None:
        return out
    earliest = start - lookback_days * DAY

    recent = _values(series.get("creatinine"), start - 7 * DAY, start)
    older = _values(series.get("creatinine"), earliest, start - 7 * DAY)
    if recent:
        out["creatinine"] = {"value": min(recent), "method": "lowest in 7 d before episode", "n": len(recent)}
    elif older:
        out["creatinine"] = {"value": round(statistics.median(older), 2),
                             "method": f"median of days 8-{lookback_days} before episode", "n": len(older)}
    for param in ("hgb", "plt", "bilirubin"):
        vals = _values(series.get(param), earliest, start)
        if vals:
            out[param] = {"value": round(statistics.median(vals), 2),
                          "method": f"median of {lookback_days} d before episode", "n": len(vals)}

    organs = {}
    if "creatinine" in out:
        organs["renal"] = scores.sofa_renal(out["creatinine"]["value"])
    if "plt" in out:
        organs["coagulation"] = scores.sofa_coagulation(out["plt"]["value"])
    if "bilirubin" in out:
        organs["liver"] = scores.sofa_liver(out["bilirubin"]["value"])
    out["sofa"] = {"components": organs, "total": sum(organs.values()), "known": bool(organs)}
    return out


def value(baseline: dict, param: str) -> float | None:
    entry = baseline.get(param)
    return entry["value"] if entry else None
