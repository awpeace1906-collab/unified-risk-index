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

KINDS = {MEASUREMENT, CONDITION, MEDICATION, CONTEXT}

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
    "urine_ml_hr": ("vital", "mL/h"),
    "etco2": ("vital", "mmHg"),
    "pain": ("vital", "{score}"),
    "qbl_ml": ("vital", "mL"),  # cumulative quantitative blood loss
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
}

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)  # time for undated facts

CONTEXT_KEYS = {"age", "cfs", "sex", "hypercapnic_target", "ob_status"}


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
