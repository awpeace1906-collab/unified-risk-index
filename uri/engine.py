"""Streaming engine: keeps per-patient state and recomputes the index on
every event.

Each recompute emits a snapshot with three index values:
  uri         - URI v2 (NEWS2 + SOFA + CRS2) plus the trajectory layer,
                never below the escalation floor: the continuously updating index
  uri_static  - URI v2 on current values only (no trend term)
  uri_v1      - the v1 formula (NEWS2 + qSOFA + MEWS + CRS), for comparison
plus component detail, trajectory signals, risk windows, correctable causes,
active risk factors, data completeness and alerts.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable, Iterable

from . import causes, scores, trends
from .config import Config
from .events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, MEDICATION, PARAMETERS, Event
from .model import HORIZONS_H, Model
from .registry import ALL, OBSTETRIC, OBSTETRIC_IDS
from .series import Series

Sink = Callable[[dict], None]


class PatientState:
    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        self.series: dict[str, Series] = {}
        self.context: dict = {}
        self.factors: dict[str, dict] = {}  # id -> {"active": bool, "source": str}
        self.meds: dict[str, dict] = {}
        self.news2_hist = Series()  # component dicts
        self.sofa_hist = Series()   # component dicts
        self.map_hist = Series()
        self.uri_hist = Series()
        self.clock: datetime | None = None
        self.last: dict | None = None

    def apply(self, e: Event) -> None:
        # Undated facts (problem list, demographics) arrive at the epoch and
        # must not move the clock: the clock is "latest clinical time seen".
        if e.time > EPOCH:
            self.clock = e.time if self.clock is None else max(self.clock, e.time)
        if e.kind == MEASUREMENT:
            self.series.setdefault(e.code, Series()).add(e.time, e.value)
        elif e.kind == CONTEXT:
            self.context[e.code] = e.value
        elif e.kind == CONDITION:
            self.factors[e.code] = {"active": e.active, "source": e.source}
        elif e.kind == MEDICATION:
            self.meds[e.code] = {"active": e.active, "source": e.source}

    def active_factors(self) -> set[str]:
        return {k for k, x in self.factors.items() if x["active"]}

    def active_meds(self) -> set[str]:
        return {k for k, x in self.meds.items() if x["active"]}


class Engine:
    def __init__(self, cfg: Config | None = None, sinks: Iterable[Sink] = (), model: Model | None = None):
        self.cfg = cfg or Config()
        self.sinks = list(sinks)
        self.model = model or (Model.load(self.cfg.model_path) if self.cfg.model_path else None)
        self.patients: dict[str, PatientState] = {}

    # -- ingestion ---------------------------------------------------------

    def ingest(self, event: Event) -> dict | None:
        """Apply one event; returns the new snapshot, or None until the
        patient has any time-stamped data."""
        return self.ingest_many([event]).get(event.patient_id)

    def ingest_many(self, events: Iterable[Event]) -> dict[str, dict]:
        """Apply a batch (e.g. one FHIR Bundle) and recompute once per patient."""
        touched: dict[str, PatientState] = {}
        for e in sorted(events, key=lambda e: e.time):
            state = self.patients.setdefault(e.patient_id, PatientState(e.patient_id))
            state.apply(e)
            touched[e.patient_id] = state
        return {pid: self._recompute(s, s.clock) for pid, s in touched.items() if s.clock is not None}

    def tick(self, at: datetime) -> dict[str, dict]:
        """Recompute every patient at wall-clock ``at`` so values age out
        (and are reported stale) even when no new data arrives."""
        out = {}
        for pid, s in self.patients.items():
            if s.clock is not None and at >= s.clock:
                s.clock = at
                out[pid] = self._recompute(s, at)
        return out

    def latest(self, patient_id: str) -> dict | None:
        s = self.patients.get(patient_id)
        return s.last if s else None

    def history(self, patient_id: str) -> list[dict]:
        s = self.patients.get(patient_id)
        if not s:
            return []
        return [{"time": t.isoformat(), "uri": u} for t, u in s.uri_hist]

    # -- scoring -----------------------------------------------------------

    def _current_values(self, s: PatientState, at: datetime) -> tuple[dict, list]:
        values, stale = {}, []
        for param, (category, _) in PARAMETERS.items():
            series = s.series.get(param)
            point = series.latest(at, self.cfg.usable(category)) if series else None
            if point is None:
                continue
            t, value = point
            values[param] = value
            if at - t > self.cfg.fresh(category):
                stale.append({"parameter": param, "age_min": int((at - t).total_seconds() // 60)})
        return values, stale

    def _recompute(self, s: PatientState, at: datetime) -> dict:
        cfg = self.cfg
        v, stale = self._current_values(s, at)
        factors, meds = s.active_factors(), s.active_meds()
        ob_active = cfg.obstetric_module and (
            "pregnancy" in factors or bool(factors & OBSTETRIC_IDS)
            or s.context.get("ob_status") in ("pregnant", "postpartum"))

        n2 = scores.news2(v, bool(s.context.get("hypercapnic_target")))
        qs = scores.qsofa(v)
        mw = scores.mews(v)
        so = scores.sofa(v, meds)
        cci = scores.charlson(factors)
        cr1 = scores.crs(v, s.context, factors, meds, n2.total, qs.total, cfg)
        cr2 = scores.crs2(v, s.context, factors, meds, cci.total, cfg, ob_active)
        uri_v1, _ = scores.composite(
            {"news2": n2.total, "qsofa": qs.total, "mews": mw.total, "crs": cr1.total}, cfg.weights_v1(), cfg)
        uri_static, breakdown = scores.composite(
            {"news2": n2.total, "sofa": so.total, "crs2": cr2.total}, cfg.weights_v2, cfg)

        # Histories the trend detectors look back over.
        s.news2_hist.add(at, dict(n2.components))
        s.sofa_hist.add(at, dict(so.components))
        m = scores.mean_arterial(v)
        if m is not None:
            # Key MAP by the time of the BP reading, not the recompute time.
            bp = s.series.get("map") or s.series.get("sbp")
            last_bp = bp.latest(at) if bp else None
            s.map_hist.add(last_bp[0] if last_bp else at, m)
        map_points = s.map_hist.window(at - timedelta(hours=4), at)

        traj_points, signals = trends.trajectory(s.series, s.news2_hist, s.sofa_hist, map_points, v,
                                                 at, cfg, ob_active)
        traj_norm = min(1.0, traj_points / cfg.trajectory_cap)
        sofa_rise = trends.component_rise(s.sofa_hist, at, timedelta(hours=48))
        floor, floor_reason = scores.floor(n2, sofa_rise, v, meds)
        uri = round(max(floor, min(100.0, uri_static + cfg.trajectory_weight * traj_norm)), 1)

        signal_ids = {x.id for x in signals}
        cause_list = causes.evaluate(causes.Facts(v, factors, meds, signal_ids, qs.total, ob_active))

        sofa_d = so.to_dict()
        sofa_d["rise_48h"] = sofa_rise
        snap = {
            "patient_id": s.patient_id,
            "time": at.isoformat(),
            "uri": uri,
            "band": scores.band(uri),
            "uri_static": uri_static,
            "uri_v1": uri_v1,
            "floor": {"value": floor, "reason": floor_reason},
            "components": breakdown,
            "scores": {"news2": n2.to_dict(), "qsofa": qs.to_dict(), "mews": mw.to_dict(),
                       "sofa": sofa_d, "charlson": cci.to_dict(), "crs": cr1.to_dict(), "crs2": cr2.to_dict()},
            "trajectory": {"points": traj_points, "normalized": round(traj_norm, 3),
                           "added": round(cfg.trajectory_weight * traj_norm, 1),
                           "signals": [x.to_dict() for x in signals]},
            "context": {k: s.context.get(k) for k in ("age", "cfs")},
            "correctable_causes": cause_list,
            "risk_factors": sorted(
                ({"id": f, "label": ALL[f].label, "domain": ALL[f].domain, "status": ALL[f].status,
                  "source": (s.factors.get(f) or s.meds.get(f))["source"]}
                 for f in (factors | meds) if f in ALL),
                key=lambda x: x["id"]),
            "data_quality": {"stale": stale,
                             "missing_core": sorted(set(n2.missing) | set(so.missing) | set(cr2.missing))},
        }
        snap["time_windows"] = self._windows(snap)
        if cfg.obstetric_module:
            snap["obstetric"] = self._obstetric(factors, ob_active)
        snap["alerts"] = self._alerts(s, snap, at)
        s.uri_hist.add(at, uri)
        s.last = snap
        for sink in self.sinks:
            sink(snap)
        return snap

    def _windows(self, snap: dict) -> list[dict]:
        if self.model is None:
            return [{**w, "calibrated": False} for w in scores.time_windows(snap["uri"], self.cfg)]
        probs = self.model.predict(snap)
        out = []
        for h in HORIZONS_H:
            pct = round(100 * probs[f"{h}h"], 1)
            out.append({"window": f"within {h}h", "estimate_pct": pct,
                        "level": scores.window_level(pct), "calibrated": True})
        return out

    def _obstetric(self, factors: set[str], active: bool) -> dict:
        present = [f for f in OBSTETRIC if f.id in factors]
        log_or = sum(f.log_odds for f in present)
        out = {
            "active": active,
            "factors": [{"id": f.id, "label": f.label, "odds_ratio": f.odds_ratio, "status": f.status}
                        for f in present],
            "combined_odds_ratio": round(math.exp(log_or), 2),
            "note": "Combined OR multiplies pooled ORs and assumes independence; uncalibrated.",
        }
        base = self.cfg.ob_baseline_pph_risk
        if active and base:
            odds = base / (1 - base) * math.exp(log_or)
            out["estimated_pph_risk"] = round(odds / (1 + odds), 3)
        return out

    def _alerts(self, s: PatientState, snap: dict, at: datetime) -> list[dict]:
        alerts = []
        prev = s.last
        # Hold alerts until a full vitals set is in, so the first set arriving
        # one Observation at a time does not look like a rapid rise.
        if snap["scores"]["news2"]["missing"]:
            return alerts
        if prev and scores.BAND_ORDER[snap["band"]] > scores.BAND_ORDER[prev["band"]]:
            alerts.append({"type": "band_escalation", "detail": f"{prev['band']} -> {snap['band']}"})
        recent = s.uri_hist.window(at - timedelta(minutes=self.cfg.rapid_rise_window_min), at)
        if recent:
            low = min(u for _, u in recent)
            if snap["uri"] - low >= self.cfg.rapid_rise_points:
                alerts.append({"type": "rapid_rise",
                               "detail": f"URI up {snap['uri'] - low:.1f} in {self.cfg.rapid_rise_window_min} min"})
        if prev:
            before = {c["cause"] for c in prev["correctable_causes"]}
            for c in snap["correctable_causes"]:
                if c["cause"] not in before:
                    alerts.append({"type": "new_correctable_cause", "detail": c["cause"]})
        return alerts
