"""Engine configuration.

Every number here is a *prior* carried over from URI v1 (the Excel tool and the
derivation manuscript) or a new, explicitly labelled default. All of them are
targets of the Phase 1 derivation / Phase 2 recalibration work and are meant to
be overridden from a JSON file rather than edited in code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import timedelta


@dataclass
class Config:
    # --- URI v2 composite (the streamed index) -----------------------------
    # One score per independent information channel, so no input is counted
    # twice: NEWS2 = bedside physiology, SOFA = organ dysfunction (labs + MAP
    # + GCS), CRS2 = biomarkers SOFA lacks + Charlson + medications + frailty.
    # These are priors; a fitted model (model_path) replaces them for the
    # risk windows.
    weights_v2: dict = field(default_factory=lambda: {"news2": 0.45, "sofa": 0.30, "crs2": 0.25})

    # Logistic model produced by `python -m uri.calibrate`. When set, the time
    # windows report fitted probabilities instead of v1 multipliers.
    model_path: str | None = None

    # --- URI v1 composite (computed alongside for comparison) ---------------
    include_mews: bool = True
    # AUROC-proportional weights (manuscript). Used when include_mews is True.
    weights_with_mews: dict = field(default_factory=lambda: {
        "news2": 0.40, "qsofa": 0.25, "mews": 0.20, "crs": 0.15})
    # Redistributed weights when MEWS is toggled off.
    weights_without_mews: dict = field(default_factory=lambda: {
        "news2": 0.50, "qsofa": 0.30, "crs": 0.20})
    # Normalization maxima. SOFA saturates at 15 (theoretical max 24): beyond
    # ~15 mortality is already very high and the extra points add nothing
    # actionable. CRS2's achievable max is 36.5 but it saturates at 30.
    score_max: dict = field(default_factory=lambda: {
        "news2": 20, "qsofa": 3, "mews": 14, "crs": 30, "sofa": 15, "crs2": 30})

    # Empiric time-window decay multipliers (v1). Not regression-derived.
    window_multipliers: dict = field(default_factory=lambda: {
        "0-6h": 1.35, "6-12h": 1.20, "12-24h": 1.05, "24-36h": 0.90, "36-48h": 0.75})
    window_cap: float = 99.0

    # --- Streaming / trend layer (new in v2) -------------------------------
    # Points the trajectory layer can add on top of the v1 index (0-100 scale).
    # Trends only ever add risk: an improving trend never pulls the index
    # below what the current physiology alone scores.
    trajectory_weight: float = 20.0
    # Trajectory points at which the trajectory layer saturates (norm = 1.0).
    trajectory_cap: float = 10.0
    # URI_stream rise (points) within rapid_rise_window that raises an alert.
    rapid_rise_points: float = 10.0
    rapid_rise_window_min: int = 60

    # How long a value is used for scoring. Labs are used for the whole open
    # visit (labs_hold_through_episode); "lab" here applies once the visit has
    # closed. When a value is *reported as stale* is decided per result by
    # uri.staleness (placeholder rules pending PubMed confirmation).
    usable_hours: dict = field(default_factory=lambda: {"vital": 12, "lab": 48})

    # --- Visits (episodes) and baselines ---------------------------------
    # The visit comes from the EHR's encounter record (ED arrival through the
    # linked admission) when one is available. Only without encounter data
    # does this fallback apply: a gap longer than this in the patient's results
    # *as a whole* starts a new visit. 36 h is a judgement call (q24h ICU labs
    # must not split a stay); Phase 1 should replace it with the empirical
    # within-encounter gap distribution. Results before the visit form the
    # patient's baseline (uri.baseline).
    episode_gap_hours: float = 36.0
    # A lab's latest value stays in use for the whole current episode, however
    # long ago it was drawn (reported as stale per uri.staleness); a
    # lab from a previous episode is never used as a current value.
    labs_hold_through_episode: bool = True
    # The two NEWS2 inputs that are often charted once and not repeated
    # (consciousness and oxygen status) carry forward the same way: the last
    # value documented in the current visit is used until a new one is
    # charted, never one from a previous visit. Older than the vital-sign
    # window, it is flagged as carried forward and reported stale. [] turns
    # this off (they then age out after usable_hours["vital"]).
    carry_forward_through_visit: list = field(default_factory=lambda: [
        "gcs", "avpu", "o2_supplemental", "o2_flow", "fio2", "resp_support"])
    baseline_lookback_days: int = 365
    # Score SOFA in the composite as acute change above the pre-episode
    # baseline (Sepsis-3), so chronic organ dysfunction is not counted as new.
    sofa_acute_in_composite: bool = True

    # --- Thresholds ------------------------------------------------------
    # Troponin 99th-percentile upper reference limit (URL) per assay, ng/L.
    # Every result is stored as a multiple of its own assay's URL, so
    # high-sensitivity and conventional assays (T or I) can be used
    # interchangeably by the rules (> 1 x URL = above the 99th percentile,
    # a relative rise >= 20 % = delta). The defaults are typical
    # manufacturer values; set each to your laboratory's assay.
    troponin_urls: dict = field(default_factory=lambda: {
        "hs_tnt": 14.0,   # high-sensitivity troponin T (e.g. Roche Elecsys)
        "hs_tni": 26.0,   # high-sensitivity troponin I (e.g. Abbott; sex-specific 16 F / 34 M)
        "tnt": 10.0,      # conventional troponin T, 0.01 ng/mL (Roche 4th generation)
        "tni": 40.0,      # conventional troponin I, 0.04 ng/mL (assay-dependent)
    })
    bnp_severe_pg_ml: float = 400.0
    ntprobnp_severe_pg_ml: float = 1800.0
    shock_index_threshold: float = 1.0

    # Location names (Encounter.location display) that count as ICU for the
    # icu_transfer outcome.
    icu_location_patterns: list = field(default_factory=lambda: [
        "icu", "intensive care", "critical care", "micu", "sicu", "ccu", "cvicu"])
    # Upper limit of normal for lipase (assay-specific), for the 3x ULN rule.
    lipase_uln_u_l: float = 60.0

    # Extra code -> parameter mappings for local flowsheet rows, e.g.
    # {"urn:oid:1.2.3|CFS": "cfs", "urn:oid:1.2.3|O2DEV": "resp_support",
    #  "urn:oid:1.2.3|RRT": "outcome:rrt"}
    local_codes: dict = field(default_factory=dict)

    def weights_v1(self) -> dict:
        return dict(self.weights_with_mews if self.include_mews else self.weights_without_mews)

    def usable(self, category: str) -> timedelta:
        return timedelta(hours=self.usable_hours[category])


    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_json(cls, path: str) -> "Config":
        with open(path) as fh:
            return cls.from_dict(json.load(fh))

    def to_dict(self) -> dict:
        return asdict(self)
