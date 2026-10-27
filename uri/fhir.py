"""FHIR R4 adapter: Observation / Condition / Medication* / Patient /
Procedure / Encounter / Consent -> Events.

Works on single resources, Bundles (transaction, collection, searchset,
history, subscription-notification) and NDJSON bulk-export lines. Unknown
resource types and codes are skipped and counted, never fatal: an EMR feed
always carries far more than the index needs.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from . import registry
from .events import (CONDITION, CONTEXT, CONTEXT_KEYS, EPOCH, MEASUREMENT, MEDICATION, OUTCOME, Event, parse_time,
                     plausible)

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
    "19123-9": "mg", "2601-3": "mg", "2777-1": "phos", "2143-6": "cortisol", "3016-3": "tsh", "4548-4": "hba1c",
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

SNOMED = "http://snomed.info/sct"
CPT = "http://www.ama-assn.org/go/cpt"
INTUBATION_CODES = {(SNOMED, "112798008"), (CPT, "31500")}  # endotracheal intubation
SURGICAL_CATEGORY = (SNOMED, "387713003")                  # surgical procedure
INPATIENT_CLASSES = {"IMP", "ACUTE", "NONAC"}
# Encounter classes that make up an acute-care visit (ED arrival through the
# linked admission). Clinic (AMB) encounters do not: their results are baseline.
VISIT_CLASSES = {"EMER", "OBSENC"} | INPATIENT_CLASSES
DEFAULT_ICU_PATTERNS = ("icu", "intensive care", "critical care", "micu", "sicu", "ccu", "cvicu", "neuro icu")
PRESSOR_START = registry.PRESSOR_AGENTS

# O2 device / ventilation mode text -> resp_support level
_SUPPORT_WORDS = [
    (4, ("ventilator", "mechanical", "endotracheal", "intubated", "tracheostomy vent", "ett")),
    (3, ("bipap", "cpap", "niv", "non-invasive", "noninvasive")),
    (2, ("high flow", "high-flow", "hfnc", "optiflow")),
    (1, ("nasal cannula", "mask", "non-rebreather", "nonrebreather", "venturi", "blow-by", "low flow")),
    (0, ("room air", "none")),
]

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


def support_level(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text or "").lower()
    for level, words in _SUPPORT_WORDS:
        if any(w in t for w in words):
            return float(level)
    return None


def _codings(codeable: dict) -> set[tuple[str, str]]:
    return {(c.get("system"), c.get("code")) for c in (codeable or {}).get("coding", [])}


def _dose_mg(r: dict) -> float | None:
    q = None
    if r["resourceType"] == "MedicationAdministration":
        q = (r.get("dosage") or {}).get("dose")
    else:
        di = (r.get("dosageInstruction") or r.get("dosage") or [{}])[0]
        q = ((di.get("doseAndRate") or [{}])[0]).get("doseQuantity")
    if not q or q.get("value") is None:
        return None
    unit = (q.get("code") or q.get("unit") or "").lower()
    return float(q["value"]) if unit == "mg" else None


class FhirAdapter:
    def __init__(self, local_codes: dict | None = None, now: datetime | None = None,
                 icu_patterns: tuple[str, ...] = DEFAULT_ICU_PATTERNS):
        self.local_codes = local_codes or {}
        self.now = now
        self.icu_patterns = tuple(p.lower() for p in icu_patterns)
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
            "Procedure": self._procedure,
            "Encounter": self._encounter,
            "Consent": self._consent,
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
            if not plausible(param, value):
                self.skipped[f"implausible:{param}"] += 1
                return []
        elif "valueBoolean" in holder:
            value = bool(holder["valueBoolean"])
        elif "valueInteger" in holder:
            value = float(holder["valueInteger"])
            if not plausible(param, value):
                self.skipped[f"implausible:{param}"] += 1
                return []
        elif "valueCodeableConcept" in holder or "valueString" in holder:
            cc = holder.get("valueCodeableConcept", {})
            value = holder.get("valueString") or cc.get("text") or \
                next((c.get("code") for c in cc.get("coding", [])), None)
        else:
            return []
        if param in CONTEXT_KEYS:  # e.g. a Clinical Frailty Scale flowsheet row
            return [Event(pid, t, CONTEXT, param, value, source=src)]
        if param.startswith("outcome:"):  # e.g. a local RRT-activation flowsheet row
            return [Event(pid, t, OUTCOME, param.split(":", 1)[1], value, source=src)]
        if param == "resp_support":
            level = support_level(value)
            if level is None:
                return []
            return [Event(pid, t, MEASUREMENT, "resp_support", level, source=src),
                    Event(pid, t, MEASUREMENT, "o2_supplemental", level >= 1, source=src)]
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
        src = f"{r['resourceType']}/{r.get('id', '')}"
        out = [Event(pid, t, MEDICATION, f.id, text.strip(), active=active, source=src) for f in classes]
        if not classes:
            self.skipped["medication:unmapped"] += 1
        # Every medication, mapped or not, counts toward polypharmacy.
        # Keyed by the first word (the ingredient, usually) so a dose change
        # does not count as a second medication.
        words = text.lower().split()
        key = words[0] if words else ""
        if key:
            out.append(Event(pid, t, MEDICATION, f"rx:{key}", text.strip(), active=active, source=src))
        lower = text.lower()
        dose = _dose_mg(r)
        for drug, factor in registry.LOOP_DIURETIC_FUROSEMIDE_EQ.items():
            if drug in lower and dose is not None and dose * factor >= 80:
                out.append(Event(pid, t, CONDITION, "high_dose_diuretic", f"{dose:g} mg {drug}",
                                 active=active, source=src))
        if r["resourceType"] == "MedicationAdministration" and active and t > EPOCH \
                and registry.is_systemic_pressor(lower):
            out.append(Event(pid, t, OUTCOME, "vasopressor_start", text.strip(), source=src))
        return out

    def _patient(self, r: dict) -> list[Event]:
        pid = r.get("id")
        if not pid:
            return []
        src = f"Patient/{pid}"
        out = []
        if r.get("birthDate"):
            born = parse_time(r["birthDate"])
            ref = self.now or datetime.now(timezone.utc)
            age = ref.year - born.year - ((ref.month, ref.day) < (born.month, born.day))
            # Demographics should not move the patient's clock forward.
            out.append(Event(pid, EPOCH, CONTEXT, "age", age, source=src))
        interpreter = any(x.get("url", "").endswith("patient-interpreterRequired") and x.get("valueBoolean")
                          for x in r.get("extension", []))
        comms = r.get("communication", [])
        preferred = [c for c in comms if c.get("preferred")] or comms[:1]
        non_english = any(not (code or "").lower().startswith("en")
                          for c in preferred for _, code in _codings(c.get("language")) if code)
        if interpreter or non_english:
            out.append(Event(pid, EPOCH, CONDITION, "language_barrier", True, source=src))
        if r.get("deceasedDateTime"):
            out.append(Event(pid, parse_time(r["deceasedDateTime"]), OUTCOME, "death", True, source=src))
        return out

    def _procedure(self, r: dict) -> list[Event]:
        pid = _patient_ref(r)
        t = _time(r, "performedDateTime", "performedPeriod")
        if not pid or not t or r.get("status") in ("entered-in-error", "not-done", "preparation"):
            return []
        src = f"Procedure/{r.get('id', '')}"
        codes = _codings(r.get("code"))
        out = []
        if codes & INTUBATION_CODES:
            out.append(Event(pid, t, OUTCOME, "intubation", True, source=src))
        if SURGICAL_CATEGORY in _codings(r.get("category")):
            out.append(Event(pid, EPOCH, CONTEXT, "last_surgery", t.isoformat(), source=src))
        mapped = self._param(r.get("code", {}))
        if mapped and mapped.startswith("outcome:"):
            out.append(Event(pid, t, OUTCOME, mapped.split(":", 1)[1], True, source=src))
        if not out:
            self.skipped["procedure:unmapped"] += 1
        return out

    def _encounter(self, r: dict) -> list[Event]:
        pid = _patient_ref(r)
        if not pid:
            return []
        src = f"Encounter/{r.get('id', '')}"
        out = []
        cls = (r.get("class") or {}).get("code")
        end = (r.get("period") or {}).get("end")
        start = (r.get("period") or {}).get("start")
        if cls in INPATIENT_CLASSES and r.get("status") == "finished" and end:
            out.append(Event(pid, EPOCH, CONTEXT, "last_inpatient_discharge", parse_time(end).isoformat(),
                             source=src))
        if cls in VISIT_CLASSES and start and r.get("status") not in ("cancelled", "entered-in-error", "planned"):
            out.append(Event(pid, EPOCH, CONTEXT, "encounter", {
                "id": r.get("id", ""), "class": cls, "start": parse_time(start).isoformat(),
                "end": parse_time(end).isoformat() if end else None}, source=src))
        for loc in r.get("location", []):
            name = ((loc.get("location") or {}).get("display") or "").lower()
            start = (loc.get("period") or {}).get("start") or (r.get("period") or {}).get("start")
            if start and any(p in name for p in self.icu_patterns):
                out.append(Event(pid, parse_time(start), OUTCOME, "icu_transfer", name, source=src))
        return out

    def _consent(self, r: dict) -> list[Event]:
        pid = _patient_ref(r)
        cats = {code for c in r.get("category", []) for _, code in _codings(c)}
        if not pid or not cats & {"acd", "42348-3"}:
            return []
        return [Event(pid, EPOCH, CONDITION, "advance_directive", True, active=r.get("status") == "active",
                      source=f"Consent/{r.get('id', '')}")]
