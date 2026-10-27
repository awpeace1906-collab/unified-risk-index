"""The normalized event model every EMR feed is translated into.

Adapters (FHIR, HL7v2, flat files) emit ``Event`` objects; the engine only ever
sees these. Values are stored in canonical units (see ``PARAMETERS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Event kinds
MEASUREMENT = "measurement"  # vital sign or lab result (numeric / bool / str)
CONDITION = "condition"      # risk factor from the problem list / diagnoses
MEDICATION = "medication"    # medication class exposure
CONTEXT = "context"          # demographics and other slowly changing facts
OUTCOME = "outcome"          # a component of the study's composite outcome

KINDS = {MEASUREMENT, CONDITION, MEDICATION, CONTEXT, OUTCOME}

# Composite decompensation outcome (study protocol): unplanned ICU transfer,
# rapid response activation, emergent intubation, vasopressor initiation, death.
OUTCOME_TYPES = {"icu_transfer", "rrt", "intubation", "vasopressor_start", "death"}

# Respiratory support ladder (resp_support values)
RESP_SUPPORT = {0: "room air", 1: "low-flow O2", 2: "high-flow nasal cannula", 3: "non-invasive ventilation",
                4: "invasive ventilation"}

# parameter -> (category, canonical unit)
PARAMETERS: dict[str, tuple[str, str]] = {
    # vitals
    "hr": ("vital", "/min"),
    "rr": ("vital", "/min"),
    "sbp": ("vital", "mmHg"),
    "dbp": ("vital", "mmHg"),
    "map": ("vital", "mmHg"),
    "spo2": ("vital", "%"),
    "temp_c": ("vital", "Cel"),
    "gcs": ("vital", "{score}"),
    "avpu": ("vital", "A|C|V|P|U"),
    "o2_supplemental": ("vital", "bool"),
    "fio2": ("vital", "%"),
    "o2_flow": ("vital", "L/min"),
    "resp_support": ("vital", "0-4"),
    "urine_ml_hr": ("vital", "mL/h"),
    "etco2": ("vital", "mmHg"),
    "pain": ("vital", "{score}"),
    # labs
    "lactate": ("lab", "mmol/L"),
    "wbc": ("lab", "10*3/uL"),
    "hgb": ("lab", "g/dL"),
    "plt": ("lab", "10*3/uL"),
    "creatinine": ("lab", "mg/dL"),
    "bun": ("lab", "mg/dL"),
    "troponin_hs": ("lab", "ng/L"),
    "bnp": ("lab", "pg/mL"),
    "ntprobnp": ("lab", "pg/mL"),
    "ddimer": ("lab", "ug/mL FEU"),
    "ph": ("lab", "[pH]"),
    "pco2": ("lab", "mmHg"),
    "po2": ("lab", "mmHg"),
    "na": ("lab", "mmol/L"),
    "k": ("lab", "mmol/L"),
    "hco3": ("lab", "mmol/L"),
    "cl": ("lab", "mmol/L"),
    "anion_gap": ("lab", "mmol/L"),
    "glucose": ("lab", "mg/dL"),
    "procalcitonin": ("lab", "ng/mL"),
    "crp": ("lab", "mg/L"),
    "inr": ("lab", "{INR}"),
    "fibrinogen": ("lab", "mg/dL"),
    "lipase": ("lab", "U/L"),
    "bilirubin": ("lab", "mg/dL"),
    "alt": ("lab", "U/L"),
    "ast": ("lab", "U/L"),
    "ammonia": ("lab", "umol/L"),
    "mg": ("lab", "mg/dL"),
    "phos": ("lab", "mg/dL"),
    "cortisol": ("lab", "ug/dL"),
    "tsh": ("lab", "m[IU]/L"),
    "hba1c": ("lab", "%"),
    # non-blood specimens (each its own parameter: a urine creatinine is not a
    # serum creatinine)
    "urine_ketones": ("lab", "mg/dL"),
    "urine_wbc": ("lab", "/[HPF]"),
    "csf_wbc": ("lab", "/uL"),
    "pleural_ph": ("lab", "[pH]"),
}

# Values outside these limits are charting or interface errors (a heart rate
# of 900, a creatinine of 0), not physiology, and are dropped on ingest. The
# limits are deliberately wide: anything a living patient could show stays.
PLAUSIBLE: dict[str, tuple[float, float]] = {
    "hr": (20, 300), "rr": (2, 80), "sbp": (30, 300), "dbp": (10, 200), "map": (20, 250), "spo2": (40, 100),
    "temp_c": (25, 45), "gcs": (3, 15), "fio2": (21, 100), "o2_flow": (0, 80), "urine_ml_hr": (0, 2000),
    "etco2": (0, 150), "pain": (0, 10), "resp_support": (0, 4),
    "lactate": (0.1, 30), "wbc": (0, 500), "hgb": (1, 25), "plt": (0, 3000), "creatinine": (0.1, 30),
    "bun": (1, 300), "troponin_hs": (0, 1e6), "bnp": (0, 1e5), "ntprobnp": (0, 1e6), "ddimer": (0, 100),
    "ph": (6.5, 8.0), "pco2": (5, 200), "po2": (10, 800), "na": (90, 200), "k": (1, 10), "hco3": (2, 60),
    "cl": (50, 160), "anion_gap": (-10, 60), "glucose": (10, 3000), "procalcitonin": (0, 1000),
    "crp": (0, 700), "inr": (0.5, 20), "fibrinogen": (20, 1500), "lipase": (0, 50000), "bilirubin": (0, 80),
    "alt": (0, 20000), "ast": (0, 20000), "ammonia": (0, 2000), "mg": (0.2, 10), "phos": (0.2, 20),
    "cortisol": (0, 200), "tsh": (0, 500), "hba1c": (3, 25),
    "urine_ketones": (0, 200), "urine_wbc": (0, 1000), "csf_wbc": (0, 100000), "pleural_ph": (6.0, 8.0),
}


def plausible(param: str, value) -> bool:
    """False for a numeric value outside the physiologically possible range."""
    lim = PLAUSIBLE.get(param)
    if lim is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    return lim[0] <= value <= lim[1]

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)  # time for undated facts

CONTEXT_KEYS = {"age", "cfs", "sex", "hypercapnic_target", "last_surgery", "last_inpatient_discharge",
                "encounter"}


def utc(dt: datetime) -> datetime:
    """Return an aware UTC datetime (naive input is assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return utc(value)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) == 10:  # FHIR date only
        text += "T00:00:00+00:00"
    return utc(datetime.fromisoformat(text))


@dataclass(frozen=True)
class Event:
    patient_id: str
    time: datetime
    kind: str
    code: str
    value: Any = None
    active: bool = True  # CONDITION / MEDICATION: False = resolved / stopped
    source: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")
        if self.kind == MEASUREMENT and self.code not in PARAMETERS:
            raise ValueError(f"unknown measurement parameter {self.code!r}")
        if self.kind == CONTEXT and self.code not in CONTEXT_KEYS:
            raise ValueError(f"unknown context key {self.code!r}")
        if self.kind == OUTCOME and self.code not in OUTCOME_TYPES:
            raise ValueError(f"unknown outcome type {self.code!r}")
        object.__setattr__(self, "time", utc(self.time))

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "time": self.time.isoformat(),
            "kind": self.kind,
            "code": self.code,
            "value": self.value,
            "active": self.active,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            patient_id=str(data["patient_id"]),
            time=parse_time(data["time"]),
            kind=data["kind"],
            code=data["code"],
            value=data.get("value"),
            active=data.get("active", True),
            source=data.get("source", ""),
        )
