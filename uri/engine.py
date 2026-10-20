"""Streaming engine: keeps per-patient state and recomputes the index on
every event.

Each recompute emits a snapshot with three index values:
  uri         - URI v2 (NEWS2 + SOFA + CRS2) plus the trajectory layer,
                never below the escalation floor: the continuously updating index
  uri_static  - URI v2 on current values only (no trend term)
  uri_v1      - the v1 formula (NEWS2 + qSOFA + MEWS + CRS), for comparison
plus component detail, trajectory signals, risk windows, correctable causes,
critical values, active risk factors, composite outcomes observed so far,
study eligibility, data completeness and alerts.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable, Iterable

import bisect

from . import baseline as baselines
from . import causes, labs, scores, staleness, trends
from .config import Config
from .events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, MEDICATION, OUTCOME, PARAMETERS, Event, parse_time
from .model import HORIZONS_H, Model
from .registry import ALL

PRESSORS = ALL["vasopressor"].ingredients
DATED_CONTEXT = {"last_surgery", "last_inpatient_discharge"}
# Outcomes stamped this long before the first measurement still count (arrival
# vs first-charting skew); anything earlier is history, not an outcome.
OUTCOME_TOLERANCE = timedelta(hours=1)
# Encounters this close together are one visit (ED discharge -> admission).
ENCOUNTER_JOIN = timedelta(hours=1)
# Results charted this long before registration (triage vitals) belong to the visit.
ARRIVAL_TOLERANCE = timedelta(hours=1)
# "Hours since measured" model features are capped here (and set here when never measured).
HOURS_SINCE_CAP = 72.0

from .series import Series  # noqa: E402

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
        self.pressor_hist = Series()
        self.outcomes: dict[str, list[datetime]] = {}  # type -> every occurrence
        self.measure_times: list[datetime] = []  # sorted, distinct
        self.encounters: dict[str, dict] = {}    # acute-care encounters (ED, observation, inpatient)
        self.clock: datetime | None = None
        self.last: dict | None = None

    def apply(self, e: Event) -> None:
        # Undated facts (problem list, demographics) arrive at the epoch and
        # must not move the clock: the clock is "latest clinical time seen".
        # Outcomes are study labels, not predictors: they never move the clock
        # (or touch the score), so they cannot leak into the prediction.
        if e.time > EPOCH and e.kind != OUTCOME:
            self.clock = e.time if self.clock is None else max(self.clock, e.time)
        if e.kind == MEASUREMENT:
            series = self.series.setdefault(e.code, Series())
            if e.code == "resp_support" and e.value == 4:
                before = series.latest(e.time)
                if before is not None and before[1] < 4:  # escalation to invasive ventilation
                    self._outcome("intubation", e.time)
            series.add(e.time, e.value)
            i = bisect.bisect_left(self.measure_times, e.time)
            if i == len(self.measure_times) or self.measure_times[i] != e.time:
                self.measure_times.insert(i, e.time)
        elif e.kind == CONTEXT:
            if e.code == "encounter":  # later updates (e.g. the end time) replace earlier ones
                self.encounters[e.value.get("id") or e.value["start"]] = e.value
            elif e.code in DATED_CONTEXT:  # keep the most recent date
                t = parse_time(e.value)
                old = self.context.get(e.code)
                self.context[e.code] = t if old is None or t > old else old
            else:
                self.context[e.code] = e.value
        elif e.kind == CONDITION:
            self.factors[e.code] = {"active": e.active, "source": e.source}
        elif e.kind == MEDICATION:
            self.meds[e.code] = {"active": e.active, "source": e.source, "text": str(e.value or "")}
        elif e.kind == OUTCOME:
            self._outcome(e.code, e.time)

    def _outcome(self, kind: str, t: datetime) -> None:
        times = self.outcomes.setdefault(kind, [])
        if t not in times:
            bisect.insort(times, t)

    def visits(self) -> list[tuple[datetime, datetime | None]]:
        """Acute-care encounters merged into visits: an ED encounter and the
        admission that follows it (within ENCOUNTER_JOIN) are one visit."""
        intervals = sorted((parse_time(v["start"]), parse_time(v["end"]) if v.get("end") else None)
                           for v in self.encounters.values())
        chains: list[tuple[datetime, datetime | None]] = []
        for s, e in intervals:
            if chains and (chains[-1][1] is None or s <= chains[-1][1] + ENCOUNTER_JOIN):
                ps, pe = chains[-1]
                chains[-1] = (ps, None if pe is None or e is None else max(pe, e))
            else:
                chains.append((s, e))
        return chains

    def episode(self, at: datetime, gap: timedelta) -> tuple[datetime | None, bool, str]:
        """(start of the current visit, is it still open, how it was determined).

        Primary: the EHR's encounter record. Fallback, when no encounter covers
        ``at``: the results-gap rule (no result of any kind for > ``gap``)."""
        for s, e in reversed(self.visits()):
            if s - ARRIVAL_TOLERANCE <= at:
                if e is None or at <= e:
                    return s - ARRIVAL_TOLERANCE, True, "encounter"
                if at - e <= gap:
                    return s - ARRIVAL_TOLERANCE, False, "encounter (discharged)"
                break
        start = baselines.episode_start(self.measure_times, at, gap)
        i = bisect.bisect_right(self.measure_times, at) - 1
        open_ = start is not None and i >= 0 and at - self.measure_times[i] <= gap
        return start, open_, "results gap (no encounter data)"

    def episode_start(self, at: datetime, gap: timedelta) -> datetime | None:
        return self.episode(at, gap)[0]

    def observed_outcomes(self, gap: timedelta, at: datetime | None = None) -> dict[str, datetime]:
        """Outcomes in the current visit, up to ``at``. Earlier ones (a prior
        admission's ICU stay pulled in with the patient's history) are
        history, not outcomes."""
        ref = at or self.clock
        start = self.episode_start(ref, gap) if ref else None
        if start is None:
            return {}
        start -= OUTCOME_TOLERANCE
        out = {}
        for kind, times in self.outcomes.items():
            first = next((t for t in times if t >= start and (at is None or t <= at)), None)
            if first is not None:
                out[kind] = first
        return out

    def active_factors(self) -> set[str]:
        return {k for k, x in self.factors.items() if x["active"]}

    def active_meds(self) -> set[str]:
        """Active medication classes (registry ids)."""
        return {k for k, x in self.meds.items() if x["active"] and not k.startswith("rx:")}

    def active_rx(self) -> dict[str, str]:
        """Every active medication, mapped or not: key -> text."""
        return {k: x["text"] for k, x in self.meds.items() if x["active"] and k.startswith("rx:")}

    def derived_factors(self, at: datetime, factors: set[str]) -> set[str]:
        out = set()
        surgery = self.context.get("last_surgery")
        if surgery and timedelta(0) <= at - surgery <= timedelta(days=30):
            out.add("recent_surgery")
        discharge = self.context.get("last_inpatient_discharge")
        if discharge and timedelta(0) <= at - discharge <= timedelta(days=90):
            out.add("recent_hospitalization")
        if len(self.active_rx()) > 5:
            out.add("polypharmacy")
        if (self.context.get("cfs") or 0) >= 5:
            out.add("frailty_dx")
        if scores.padua(factors | out, self.context.get("age")).total >= 4:
            out.add("pe_risk")
        return out

    def n_pressors(self) -> int:
        """Distinct vasopressor / inotrope agents currently active."""
        agents = set()
        for text in self.active_rx().values():
            for p in PRESSORS:
                # word-start match so "norepinephrine" does not also count as "epinephrine"
                if re.search(r"(?<![a-z])" + re.escape(p), text.lower()):
                    agents.add(p)
        return len(agents)


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
            if e.kind != OUTCOME:  # labels alone do not change the score
                touched[e.patient_id] = state
        return {pid: self._recompute(s, s.clock) for pid, s in touched.items() if s.clock is not None}

    def preload(self, events: Iterable[Event]) -> int:
        """Apply prior history (arrival-time pull) without recomputing, so no
        snapshot is emitted for times before the patient's current visit.
        Returns the number of events applied."""
        n = 0
        for e in sorted(events, key=lambda e: e.time):
            self.patients.setdefault(e.patient_id, PatientState(e.patient_id)).apply(e)
            n += 1
        return n

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

    def outcomes(self) -> list[tuple[str, str, datetime]]:
        """(patient_id, outcome type, time) for every observed composite outcome."""
        gap = timedelta(hours=self.cfg.episode_gap_hours)
        return sorted(((pid, k, t) for pid, s in self.patients.items()
                       for k, t in s.observed_outcomes(gap).items()), key=lambda x: (x[0], x[2]))

    def history(self, patient_id: str) -> list[dict]:
        s = self.patients.get(patient_id)
        if not s:
            return []
        return [{"time": t.isoformat(), "uri": u} for t, u in s.uri_hist]

    # -- scoring -----------------------------------------------------------

    def _current_values(self, s: PatientState, at: datetime, start: datetime | None,
                        visit_open: bool) -> tuple[dict, dict]:
        """(value, time measured) per parameter. Labs are held for the whole
        open visit and never taken from an earlier one; once the visit has
        closed (discharge, or results stopped), they age out normally."""
        values, times = {}, {}
        hold = self.cfg.labs_hold_through_episode and start is not None and visit_open
        for param, (category, _) in PARAMETERS.items():
            series = s.series.get(param)
            if series is None:
                continue
            if category == "lab" and hold:
                point = series.latest(at)
                if point is not None and point[0] < start:
                    point = None
            else:
                point = series.latest(at, self.cfg.usable(category))
            if point is None:
                continue
            times[param], values[param] = point
        return values, times

    def _staleness(self, s: PatientState, v: dict, times: dict, at: datetime, n2) -> list[dict]:
        """Per-result windows from uri.staleness (placeholders pending PubMed
        confirmation); vitals follow the NEWS2 monitoring frequency."""
        dropping = trends.hgb_drop(s.series.get("hgb"), at) is not None
        out = []
        for param, t in times.items():
            rule = staleness.window(param, v, n2.total if not n2.missing else None,
                                    bool(n2.flags), dropping)
            if rule is None:
                continue
            hours, why = rule
            age = at - t
            if age > timedelta(hours=hours):
                out.append({"parameter": param, "age_min": int(age.total_seconds() // 60),
                            "window_h": hours, "rule": why, "rule_status": staleness.rule_for(param).status})
        return out

    @staticmethod
    def _hours_since(times: dict, at: datetime) -> dict:
        def since(params):
            ts = [times[p] for p in params if p in times]
            if not ts:
                return HOURS_SINCE_CAP
            return round(min(HOURS_SINCE_CAP, (at - max(ts)).total_seconds() / 3600), 2)
        return {"vitals": since(("hr", "rr", "sbp", "spo2")), "lactate": since(("lactate",)),
                "hgb": since(("hgb",)), "creatinine": since(("creatinine",))}

    def _recompute(self, s: PatientState, at: datetime) -> dict:
        cfg = self.cfg
        gap = timedelta(hours=cfg.episode_gap_hours)
        start, visit_open, episode_source = s.episode(at, gap)
        v, times = self._current_values(s, at, start, visit_open)
        factors, meds = s.active_factors(), s.active_meds()
        factors |= s.derived_factors(at, factors)
        n_pressors = s.n_pressors()
        s.pressor_hist.add(at, n_pressors)

        n2 = scores.news2(v, self._hypercapnic(s, at, factors))
        qs = scores.qsofa(v)
        mw = scores.mews(v)
        so = scores.sofa(v, meds, n_pressors)
        base = baselines.compute(s.series, start, cfg.baseline_lookback_days)
        base_sofa = base.get("sofa", {"components": {}, "total": 0, "known": False})
        sofa_acute = scores.acute_sofa(so.components, base_sofa["components"])
        pad = scores.padua(factors, s.context.get("age"))
        cci = scores.charlson(factors)
        cr1 = scores.crs(v, s.context, factors, meds, n2.total, qs.total, cfg)
        cr2 = scores.crs2(v, s.context, factors, meds, cci.total, cfg)
        uri_v1, _ = scores.composite(
            {"news2": n2.total, "qsofa": qs.total, "mews": mw.total, "crs": cr1.total}, cfg.weights_v1(), cfg)
        uri_static, breakdown = scores.composite(
            {"news2": n2.total, "sofa": sofa_acute if cfg.sofa_acute_in_composite else so.total,
             "crs2": cr2.total}, cfg.weights_v2, cfg)

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

        above_baseline = sofa_acute if base_sofa["known"] else None
        traj_points, signals = trends.trajectory(s.series, s.news2_hist, s.sofa_hist, map_points, v,
                                                 at, cfg, s.pressor_hist, base, above_baseline)
        traj_norm = min(1.0, traj_points / cfg.trajectory_cap)
        sofa_rise = trends.component_rise(s.sofa_hist, at, timedelta(hours=48))
        sofa_delta = max(sofa_rise or 0, above_baseline or 0)
        floor, floor_reason = scores.floor(n2, sofa_delta, v, meds)
        uri = round(max(floor, min(100.0, uri_static + cfg.trajectory_weight * traj_norm)), 1)

        signal_ids = {x.id for x in signals}
        cause_list = causes.evaluate(causes.Facts(v, factors, meds, signal_ids, qs.total, cfg.lipase_uln_u_l))

        sofa_d = so.to_dict()
        sofa_d["rise_48h"] = sofa_rise
        sofa_d["baseline"] = base_sofa["total"] if base_sofa["known"] else None
        sofa_d["acute"] = sofa_acute
        snap = {
            "patient_id": s.patient_id,
            "time": at.isoformat(),
            "episode_start": base.get("episode_start"),
            "visit": {"start": start.isoformat() if start else None, "open": visit_open,
                      "determined_by": episode_source},
            "uri": uri,
            "band": scores.band(uri),
            "uri_static": uri_static,
            "uri_v1": uri_v1,
            "floor": {"value": floor, "reason": floor_reason},
            "baseline": base,
            "components": breakdown,
            "scores": {"news2": n2.to_dict(), "qsofa": qs.to_dict(), "mews": mw.to_dict(),
                       "sofa": sofa_d, "charlson": cci.to_dict(), "padua": pad.to_dict(), "crs": cr1.to_dict(), "crs2": cr2.to_dict()},
            "trajectory": {"points": traj_points, "normalized": round(traj_norm, 3),
                           "added": round(cfg.trajectory_weight * traj_norm, 1),
                           "signals": [x.to_dict() for x in signals]},
            "context": {"age": s.context.get("age"), "cfs": s.context.get("cfs"),
                        "hours_since": self._hours_since(times, at),
                        "news2_spo2_scale": 2 if self._hypercapnic(s, at, factors) else 1,
                        "resp_support": v.get("resp_support"), "n_pressors": n_pressors,
                        "active_medications": len(s.active_rx())},
            "correctable_causes": cause_list,
            "critical_values": labs.critical_values(v, s.context, cfg),
            "risk_factors": sorted(
                ({"id": f, "label": ALL[f].label, "domain": ALL[f].domain, "status": ALL[f].status,
                  "source": (s.factors.get(f) or s.meds.get(f) or {}).get("source")
                  or f"derived: {ALL[f].derived}"}
                 for f in (factors | meds) if f in ALL),
                key=lambda x: x["id"]),
            "data_quality": {"stale": self._staleness(s, v, times, at, n2),
                             "missing_core": sorted(set(n2.missing) | set(so.missing) | set(cr2.missing))},
        }
        snap["outcomes"] = [{"type": k, "time": t.isoformat()}
                            for k, t in sorted(s.observed_outcomes(gap, at).items(), key=lambda x: x[1])]
        snap["study_eligibility"] = self._eligibility(s, factors)
        snap["time_windows"] = self._windows(snap)
        snap["alerts"] = self._alerts(s, snap, at)
        s.uri_hist.add(at, uri)
        s.last = snap
        for sink in self.sinks:
            sink(snap)
        return snap

    @staticmethod
    def _hypercapnic(s: PatientState, at: datetime, factors: set[str]) -> bool:
        """NEWS2 SpO2 Scale 2 (target 88-92%) is for confirmed hypercapnic
        respiratory failure (RCP 2017): charted explicitly, or inferred from
        COPD / OSA plus a blood gas with pCO2 > 45 mmHg."""
        if s.context.get("hypercapnic_target") is not None:
            return bool(s.context["hypercapnic_target"])
        pco2 = s.series.get("pco2")
        return bool(factors & {"copd", "osa"}) and pco2 is not None and \
            any(x > 45 for _, x in pco2.window(EPOCH, at) if isinstance(x, (int, float)))

    @staticmethod
    def _eligibility(s: PatientState, factors: set[str]) -> dict:
        """Protocol exclusions that can be read from the stream. ICU orders at
        triage and immediate OR transfer need ADT/order data and are not checked."""
        reasons = []
        age = s.context.get("age")
        if age is not None and age < 18:
            reasons.append("age < 18")
        if "dnr" in factors:
            reasons.append("DNR/DNI on file: exclude if no treatment intent (needs review)")
        return {"eligible": not reasons, "reasons": reasons}

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
