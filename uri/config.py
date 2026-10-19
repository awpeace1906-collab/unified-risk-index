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
    # --- URI v1 composite -------------------------------------------------
    include_mews: bool = True
    # AUROC-proportional weights (manuscript). Used when include_mews is True.
    weights_with_mews: dict = field(default_factory=lambda: {
        "news2": 0.40, "qsofa": 0.25, "mews": 0.20, "crs": 0.15})
    # Redistributed weights when MEWS is toggled off.
    weights_without_mews: dict = field(default_factory=lambda: {
        "news2": 0.50, "qsofa": 0.30, "crs": 0.20})
    score_max: dict = field(default_factory=lambda: {
        "news2": 20, "qsofa": 3, "mews": 14, "crs": 30})

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

    # How long a value is used for scoring, and when it is reported as stale.
    usable_hours: dict = field(default_factory=lambda: {"vital": 12, "lab": 48})
    fresh_hours: dict = field(default_factory=lambda: {"vital": 2, "lab": 24})

    # --- Thresholds ------------------------------------------------------
    # hs-troponin 99th percentile URL is assay-specific; set to your assay.
    troponin_url_ng_l: float = 14.0
    bnp_severe_pg_ml: float = 400.0
    ntprobnp_severe_pg_ml: float = 1800.0
    shock_index_threshold: float = 1.0
    obstetric_shock_index_threshold: float = 0.9

    # --- Obstetric hemorrhage module ---------------------------------------
    # CRS points per unit of summed ln(OR), and the cap for that domain.
    ob_points_per_log_or: float = 2.0
    ob_points_cap: float = 6.0
    # Baseline PPH risk for the local population. Leave None until it comes
    # from local data; the module then reports combined odds ratio only.
    ob_baseline_pph_risk: float | None = None

    # Extra code -> parameter mappings for local flowsheet rows, e.g.
    # {"urn:oid:1.2.3|QBL": "qbl_ml", "urn:local|CFS": "cfs"}
    local_codes: dict = field(default_factory=dict)

    def weights(self) -> dict:
        return dict(self.weights_with_mews if self.include_mews else self.weights_without_mews)

    def usable(self, category: str) -> timedelta:
        return timedelta(hours=self.usable_hours[category])

    def fresh(self, category: str) -> timedelta:
        return timedelta(hours=self.fresh_hours[category])

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
