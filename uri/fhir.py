"""FHIR R4 adapter: Observation / Condition / Medication* / Patient -> Events.

Works on single resources, Bundles (transaction, collection, searchset,
history, subscription-notification) and NDJSON bulk-export lines. Unknown
resource types and codes are skipped and counted, never fatal: an EMR feed
always carries far more than the index needs.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from . import registry
from .events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, MEDICATION, Event, parse_time

LOINC = "http://loinc.org"

LOINC_MAP: dict[str, str] = {
    # vitals
    "8867-4": "hr", "9279-1": "rr", "8480-6": "sbp", "8462-4": "dbp", "8478-0": "map",
    "59408-5": "spo2", "2708-6": "spo2", "8310-5": "temp_c", "9269-2": "gcs",
    "3150-0": "fio2", "3151-8": "o2_flow", "72514-3": "pain", "19889-5": "etco2",
    # labs
    "2524-7": "lactate", "32693-4": "lactate", "6690-2": "wbc", "718-7": "hgb", "777-3": "plt",
    "2160-0": "creatinine", "3094-0": "bun", "67151-1": "troponin_hs", "89579-7": "troponin_hs",
    "30934-4": "bnp", "42637-9": "bnp", "33762-6": "ntprobnp", "48065-7": "ddimer",
    "2744-1": "ph", "2746-6": "ph", "2019-8": "pco2", "2703-7": "po2",
    "2951-2": "na", "2823-3": "k", "6298-4": "k", "1963-8": "hco3", "2028-9": "hco3",
    "2075-0": "cl", "33037-3": "anion_gap", "1863-0": "anion_gap", "2345-7": "glucose", "2339-0": "glucose",
    "33959-8": "procalcitonin", "1988-5": "crp", "6301-6": "inr", "34714-6": "inr", "3255-7": "fibrinogen",
    "3040-3": "lipase", "1975-2": "bilirubin", "1742-6": "alt", "1920-8": "ast", "16362-6": "ammonia",
    "19123-9": "mg", "2601-3": "mg", "2777-1": "phos", "2143-6": "cortisol", "3016-3": "tsh",
}
BP_PANEL = "85354-9"

# (parameter, unit as sent) -> factor to canonical unit
_FACTORS = {
    ("glucose", "mmol/l"): 18.016,
    ("creatinine", "umol/l"): 1 / 88.42, ("creatinine", "µmol/l"): 1 / 88.42,
    ("hgb", "g/l"): 0.1,
    ("troponin_hs", "ng/ml"): 1000, ("troponin_hs", "ug/l"): 1000, ("troponin_hs", "µg/l"): 1000,
    ("lactate", "mg/dl"): 1 / 9.008,
    ("fibrinogen", "g/l"): 100,
    ("bun", "mmol/l"): 2.8,
}

_ACTIVE = {"active", "recurrence", "relapse", "in-progress", "on-hold", "intended"}
_SKIP_OBS = {"entered-in-error", "cancelled", "registered"}


def _patient_ref(resource: dict) -> str | None:
    ref = (resource.get("subject") or resource.get("patient") or {}).get("reference", "")
    return ref.split("/")[-1] if ref else None


def _time(resource: dict, *keys) -> datetime | None:
    for k in keys:
        val = resource.get(k)
        if isinstance(val, dict):
            val = val.get("start")
        if val:
            return parse_time(val)
    return None


def _convert(param: str, value: float, unit: str) -> float:
    u = (unit or "").strip().lower()
    if param == "temp_c" and u in ("[degf]", "degf", "°f", "f"):
        return round((value - 32) * 5 / 9, 2)
    if param == "fio2" and value <= 1.0:
        return value * 100
    return value * _FACTORS.get((param, u), 1)


class FhirAdapter:
    def __init__(self, local_codes: dict | None = None, now: datetime | None = None):
        self.local_codes = local_codes or {}
        self.now = now
        self.skipped = Counter()

    # -- entry points --------------------------------------------------------

    def events(self, payload: dict) -> list[Event]:
        if payload.get("resourceType") == "Bundle":
            out = []
            for entry in payload.get("entry", []):
                if entry.get("resource"):
                    out += self.events(entry["resource"])
            return out
        handler = {
            "Observation": self._observation,
            "Condition": self._condition,
            "MedicationRequest": self._medication,
            "MedicationStatement": self._medication,
            "MedicationAdministration": self._medication,
            "Patient": self._patient,
        }.get(payload.get("resourceType"))
        if handler is None:
            self.skipped[f"resource:{payload.get('resourceType')}"] += 1
            return []
        return handler(payload)

    # -- resources -------------------------------------------------------------

    def _param(self, codeable: dict) -> str | None:
        for c in codeable.get("coding", []):
            key = f"{c.get('system')}|{c.get('code')}"
            if key in self.local_codes:
                return self.local_codes[key]
            if c.get("system") == LOINC and c.get("code") in LOINC_MAP:
                return LOINC_MAP[c["code"]]
            if c.get("system") == LOINC and c.get("code") == BP_PANEL:
                return "bp_panel"
        return None

    def _observation(self, r: dict) -> list[Event]:
        if r.get("status") in _SKIP_OBS:
            self.skipped["observation:status"] += 1
            return []
        pid = _patient_ref(r)
        t = _time(r, "effectiveDateTime", "effectivePeriod", "effectiveInstant", "issued")
        if not pid or not t:
            self.skipped["observation:no-subject-or-time"] += 1
            return []
        param = self._param(r.get("code", {}))
        src = f"Observation/{r.get('id', '')}"
        if param == "bp_panel" or (param is None and r.get("component")):
            out = []
            for comp in r.get("component", []):
                p = self._param(comp.get("code", {}))
                out += self._value_events(pid, t, p, comp, src)
            return out
        if param is None:
            self.skipped["observation:unmapped-code"] += 1
            return []
        return self._value_events(pid, t, param, r, src)

    def _value_events(self, pid, t, param, holder, src) -> list[Event]:
        if param is None:
            return []
        if "valueQuantity" in holder:
            q = holder["valueQuantity"]
            if q.get("value") is None:
                return []
            value = _convert(param, float(q["value"]), q.get("code") or q.get("unit", ""))
        elif "valueBoolean" in holder:
            value = bool(holder["valueBoolean"])
        elif "valueInteger" in holder:
            value = float(holder["valueInteger"])
        elif "valueCodeableConcept" in holder or "valueString" in holder:
            cc = holder.get("valueCodeableConcept", {})
            value = holder.get("valueString") or cc.get("text") or \
                next((c.get("code") for c in cc.get("coding", [])), None)
        else:
            return []
        if param == "o2_flow":  # any O2 flow means supplemental oxygen
            return [Event(pid, t, MEASUREMENT, "o2_flow", float(value), source=src),
                    Event(pid, t, MEASUREMENT, "o2_supplemental", float(value) > 0, source=src)]
        return [Event(pid, t, MEASUREMENT, param, value, source=src)]

    def _condition(self, r: dict) -> list[Event]:
        pid = _patient_ref(r)
        if not pid:
            return []
        status = next((c.get("code") for c in (r.get("clinicalStatus") or {}).get("coding", [])), "active")
        if (r.get("verificationStatus") or {}).get("coding", [{}])[0].get("code") in ("refuted", "entered-in-error"):
            status = "inactive"
        t = _time(r, "recordedDate", "onsetDateTime", "onsetPeriod") or EPOCH
        out = []
        for c in r.get("code", {}).get("coding", []):
            if "icd-10" not in (c.get("system") or "").lower():
                continue
            for f in registry.factors_for_icd10(c.get("code", "")):
                out.append(Event(pid, t, CONDITION, f.id, c.get("code"), active=status in _ACTIVE,
                                 source=f"Condition/{r.get('id', '')} {c.get('code')}"))
        if not out:
            self.skipped["condition:unmapped"] += 1
        return out

    def _medication(self, r: dict) -> list[Event]:
        pid = _patient_ref(r)
        if not pid:
            return []
        cc = r.get("medicationCodeableConcept", {})
        text = " ".join([cc.get("text", "")] + [c.get("display", "") for c in cc.get("coding", [])])
        t = _time(r, "authoredOn", "effectiveDateTime", "effectivePeriod", "dateAsserted") or EPOCH
        live = _ACTIVE | {"completed"} if r["resourceType"] == "MedicationAdministration" else _ACTIVE
        active = r.get("status", "active") in live
        classes = registry.medication_classes(text)
        if not classes:
            self.skipped["medication:unmapped"] += 1
        return [Event(pid, t, MEDICATION, f.id, text.strip(), active=active,
                      source=f"{r['resourceType']}/{r.get('id', '')}") for f in classes]

    def _patient(self, r: dict) -> list[Event]:
        pid = r.get("id")
        if not pid or not r.get("birthDate"):
            return []
        born = parse_time(r["birthDate"])
        ref = self.now or datetime.now(timezone.utc)
        age = ref.year - born.year - ((ref.month, ref.day) < (born.month, born.day))
        # Demographics should not move the patient's clock forward.
        return [Event(pid, EPOCH, CONTEXT, "age", age, source=f"Patient/{pid}")]
