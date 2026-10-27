"""MIMIC-IV-ED + MIMIC-IV -> URI event stream, Phase 0 audit, replay and calibration.

    python -m uri.mimic run --mimic-dir ~/physionet/mimiciv/3.1 --ed-dir ~/physionet/mimic-iv-ed/2.2 \\
        --out data/mimic [--limit 20000] [--seed 1] [--icd9-gem 2018_I9gem.txt] [--exclude-direct-icu]
    python -m uri.mimic demo-data data/mimic-demo     # synthetic tables in the MIMIC layout, for a dry run

``run`` does, in one pass:

1. **extract**: one URI patient per ED stay (``<subject_id>-<stay_id>``), with
   ED triage and vital signs, labs (MIMIC-IV hosp ``labevents``), home and ED
   medications, prior-admission diagnoses, demographics, the visit
   (encounter) boundaries, and the composite outcome. Events go to a SQLite
   file (``events.sqlite``) so the full cohort never has to sit in memory.
2. **audit**: ``audit.csv`` in the Phase 0 template layout, built from the
   tables directly (not from the engine), then the Phase 0 report
   (``uri.audit``) with event rates, the decision rule and sample sizes.
3. **replay**: every stay through the streaming engine. Writes
   ``snapshots.jsonl`` (one compact snapshot per stay-hour, 0-48 h),
   ``outcomes.csv`` and ``encounters.csv``.
4. **calibrate** (if numpy is installed): ``model.json`` and the AUROC report
   against NEWS2 alone and URI v1.

Mapping decisions (see docs/MIMIC.md for the reasoning and limitations):

* Predictors come from the ED stay up to 48 h after arrival. Diagnoses come
  only from admissions discharged *before* the ED arrival: the index
  admission's codes are assigned at discharge and would leak the outcome.
  ICD-9 codes are mapped through a CMS GEM file when given (``--icd9-gem``).
* Baseline labs (creatinine, Hgb, platelets, bilirubin, pCO2) are pulled
  from the 365 days before arrival and preloaded as history.
* Outcomes: ICU transfer (``transfers`` care unit), intubation (ICU
  ``procedureevents``), vasopressor start (ICU ``inputevents``; ED ``pyxis``
  and ward ``emar`` for norepinephrine, vasopressin and dopamine only, since
  epinephrine and phenylephrine are mostly IM / push / topical outside the
  ICU), and in-hospital death. MIMIC has no rapid-response record.

The PhysioNet data use agreement governs where this data may be processed.
Run this on a machine the agreement allows. Output stays under ``data/``,
which is git-ignored.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import registry
from .config import Config
from .engine import Engine
from .events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, MEDICATION, OUTCOME, Event, utc
from .fhir import _convert

H = timedelta(hours=1)
FOLLOW_UP = 48 * H
BASELINE_LOOKBACK = timedelta(days=365)
BASELINE_PARAMS = {"creatinine", "hgb", "plt", "bilirubin", "pco2"}

# d_labitems label (lower case) -> parameter; blood specimens only
LAB_LABELS = {
    "lactate": "lactate", "white blood cells": "wbc", "wbc count": "wbc", "hemoglobin": "hgb",
    "platelet count": "plt", "creatinine": "creatinine", "urea nitrogen": "bun", "sodium": "na",
    "potassium": "k", "chloride": "cl", "bicarbonate": "hco3", "anion gap": "anion_gap", "glucose": "glucose",
    "ph": "ph", "pco2": "pco2", "po2": "po2", "troponin t": "troponin_hs", "ntprobnp": "ntprobnp",
    "inr(pt)": "inr", "fibrinogen, functional": "fibrinogen", "lipase": "lipase",
    "bilirubin, total": "bilirubin", "alanine aminotransferase (alt)": "alt",
    "asparate aminotransferase (ast)": "ast", "aspartate aminotransferase (ast)": "ast", "ammonia": "ammonia",
    "magnesium": "mg", "phosphate": "phos", "c-reactive protein": "crp", "d-dimer": "ddimer",
    "procalcitonin": "procalcitonin", "cortisol": "cortisol", "thyroid stimulating hormone": "tsh",
    "% hemoglobin a1c": "hba1c",
}
_MIMIC_FACTORS = {("ddimer", "ng/ml feu"): 0.001, ("ddimer", "ng/ml"): 0.001}

# ED vital-sign columns -> parameter, with plausibility limits (charting errors are common)
ED_VITALS = {"heartrate": "hr", "resprate": "rr", "o2sat": "spo2", "sbp": "sbp", "dbp": "dbp",
             "temperature": "temp_c", "pain": "pain"}
LIMITS = {"hr": (20, 250), "rr": (4, 70), "spo2": (50, 100), "sbp": (40, 300), "dbp": (15, 200),
          "temp_c": (25, 45), "pain": (0, 10)}

ICU_UNIT_WORDS = ("intensive care", "coronary care", "micu", "sicu", "cvicu")
PRESSOR_ITEMIDS = {"221906": "norepinephrine", "221289": "epinephrine", "222315": "vasopressin",
                   "221662": "dopamine", "221749": "phenylephrine"}
INTUBATION_ITEMIDS = {"224385"}
WARD_PRESSORS = ("norepinephrine", "vasopressin", "dopamine")
EMAR_GIVEN = {"administered", "started", "new bag", "restarted"}


# --- reading -------------------------------------------------------------------

def find_table(roots: list[str], name: str) -> str | None:
    for root in roots:
        for dirpath, _, files in os.walk(root):
            for ext in (".csv.gz", ".csv"):
                if name + ext in files:
                    return os.path.join(dirpath, name + ext)
    return None


def read_rows(path: str):
    fh = gzip.open(path, "rt", newline="") if path.endswith(".gz") else open(path, newline="")
    with fh:
        yield from csv.DictReader(fh)


def ts(text: str | None) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    return utc(datetime.fromisoformat(text))


def _num(text) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def vital(param: str, raw) -> float | None:
    x = _num(raw)
    if x is None:
        return None
    if param == "temp_c":
        if 77 <= x <= 113:  # MIMIC-IV-ED charts Fahrenheit
            x = round((x - 32) * 5 / 9, 2)
    lo, hi = LIMITS[param]
    return x if lo <= x <= hi else None


def load_gem(path: str | None) -> dict[str, list[str]]:
    """CMS General Equivalence Mapping, ICD-9-CM -> ICD-10-CM (2018_I9gem.txt)."""
    gem = defaultdict(list)
    if path:
        with open(path) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "NoDx":
                    gem[parts[0]].append(parts[1])
    return gem


# --- cohort ------------------------------------------------------------------

@dataclass
class Stay:
    subject: str
    stay_id: str
    hadm: str
    intime: datetime
    outtime: datetime | None
    disposition: str
    age: float | None = None
    sex: str | None = None
    end: datetime | None = None          # encounter end (hospital discharge if admitted)
    death: datetime | None = None
    revisit_72h: bool = False
    direct_icu: bool = False
    outcomes: dict = field(default_factory=dict)

    @property
    def pid(self) -> str:
        return f"{self.subject}-{self.stay_id}"

    def note(self, kind: str, t: datetime) -> None:
        if t >= self.intime and (kind not in self.outcomes or t < self.outcomes[kind]):
            self.outcomes[kind] = t


class Store:
    """Events in SQLite, keyed by patient and time; history rows are flagged."""

    def __init__(self, path: str):
        if os.path.exists(path):
            os.remove(path)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE ev (pid TEXT, t TEXT, hist INT, kind TEXT, code TEXT, value TEXT, "
                        "active INT, source TEXT)")
        self.buf = []
        self.n = 0

    def add(self, stay: Stay, e: Event) -> None:
        hist = int(e.time < stay.intime)
        self.buf.append((e.patient_id, e.time.isoformat(), hist, e.kind, e.code, json.dumps(e.value),
                         int(e.active), e.source))
        if len(self.buf) >= 50_000:
            self.flush()

    def flush(self) -> None:
        self.db.executemany("INSERT INTO ev VALUES (?,?,?,?,?,?,?,?)", self.buf)
        self.n += len(self.buf)
        self.buf = []

    def close(self) -> None:
        self.flush()
        self.db.execute("CREATE INDEX ev_pid ON ev (pid, t)")
        self.db.commit()

    def patients(self):
        """(pid, history events, current events) per patient, in time order."""
        cur = self.db.execute("SELECT pid, t, hist, kind, code, value, active, source FROM ev ORDER BY pid, t")
        pid, hist, now = None, [], []
        for row in cur:
            if row[0] != pid and pid is not None:
                yield pid, hist, now
                hist, now = [], []
            pid = row[0]
            e = Event(row[0], datetime.fromisoformat(row[1]), row[3], row[4], json.loads(row[5]), bool(row[6]),
                      row[7])
            (hist if row[2] else now).append(e)
        if pid is not None:
            yield pid, hist, now


class Extractor:
    def __init__(self, mimic_dir: str, ed_dir: str | None = None, limit: int | None = None, seed: int = 1,
                 gem_path: str | None = None, log=None):
        self.roots = [d for d in (ed_dir, mimic_dir) if d]
        self.limit, self.seed = limit, seed
        self.gem = load_gem(gem_path)
        self.log = log or sys.stderr
        self.stays: dict[str, Stay] = {}
        self.by_subject: dict[str, list[Stay]] = defaultdict(list)
        self.counts = defaultdict(int)

    def table(self, name: str, required: bool = True) -> str | None:
        path = find_table(self.roots, name)
        if path is None and required:
            raise FileNotFoundError(f"MIMIC table {name}.csv(.gz) not found under {self.roots}")
        if path is None:
            print(f"  {name}: not found, skipped", file=self.log)
        return path

    def rows(self, name: str, required: bool = True):
        path = self.table(name, required)
        if path is None:
            return
        print(f"  reading {name}", file=self.log)
        for row in read_rows(path):
            if row.get("subject_id") in self.by_subject:
                yield row

    def stays_at(self, subject: str, t: datetime, before: timedelta = timedelta(0)):
        """Stays whose window [intime - before, intime + 48 h] contains t."""
        out = []
        for s in self.by_subject.get(subject, ()):
            if s.intime - before <= t <= s.intime + FOLLOW_UP:
                out.append(s)
        return out

    # -- steps ----------------------------------------------------------------

    def cohort(self) -> None:
        all_stays = []
        for row in read_rows(self.table("edstays")):
            intime = ts(row["intime"])
            if intime is None:
                continue
            all_stays.append(Stay(row["subject_id"], row["stay_id"], (row.get("hadm_id") or "").strip(), intime,
                                  ts(row.get("outtime")), (row.get("disposition") or "").upper()))
        by_subj = defaultdict(list)
        for s in all_stays:
            by_subj[s.subject].append(s)
        for stays in by_subj.values():
            stays.sort(key=lambda s: s.intime)
            for a, b in zip(stays, stays[1:]):
                if a.outtime and timedelta(0) <= b.intime - a.outtime <= 72 * H:
                    a.revisit_72h = True
        chosen = all_stays
        if self.limit and self.limit < len(all_stays):
            chosen = random.Random(self.seed).sample(all_stays, self.limit)
        for s in sorted(chosen, key=lambda s: s.intime):
            s.end = s.outtime
            if s.disposition == "EXPIRED" and s.outtime:
                s.death = s.outtime
            self.stays[s.pid] = s
            self.by_subject[s.subject].append(s)
        print(f"cohort: {len(self.stays)} ED stays of {len(all_stays)}", file=self.log)

    def demographics(self, store: Store) -> None:
        for row in self.rows("patients"):
            for s in self.by_subject[row["subject_id"]]:
                age = _num(row.get("anchor_age"))
                year = _num(row.get("anchor_year"))
                if age is not None and year is not None:
                    s.age = age + s.intime.year - year
                    store.add(s, Event(s.pid, EPOCH, CONTEXT, "age", s.age, source="patients"))
                s.sex = (row.get("gender") or "").upper() or None
                if s.sex:
                    store.add(s, Event(s.pid, EPOCH, CONTEXT, "sex", s.sex, source="patients"))
        prior = defaultdict(list)  # subject -> [(dischtime, hadm)]
        for row in self.rows("admissions"):
            disch = ts(row.get("dischtime"))
            for s in self.by_subject[row["subject_id"]]:
                if row["hadm_id"] == s.hadm:
                    s.end = disch or s.end
                    death = ts(row.get("deathtime"))
                    if death:
                        s.death = death
            if disch:
                prior[row["subject_id"]].append((disch, row["hadm_id"]))
        self.prior_admissions = prior
        for subject, stays in self.by_subject.items():
            for s in stays:
                before = [d for d, _ in prior.get(subject, ()) if d < s.intime]
                if before:
                    store.add(s, Event(s.pid, EPOCH, CONTEXT, "last_inpatient_discharge", max(before).isoformat(),
                                       source="admissions"))
                store.add(s, Event(s.pid, EPOCH, CONTEXT, "encounter", {
                    "id": s.stay_id, "class": "EMER", "start": s.intime.isoformat(),
                    "end": s.end.isoformat() if s.end else None}, source="edstays"))

    def ed_vitals(self, store: Store) -> None:
        stay_index = {s.stay_id: s for s in self.stays.values()}
        for name, time_col in (("triage", None), ("vitalsign", "charttime")):
            for row in self.rows(name):
                s = stay_index.get(row.get("stay_id"))
                if s is None:
                    continue
                t = s.intime if time_col is None else ts(row.get(time_col))
                if t is None or t > s.intime + FOLLOW_UP:
                    continue
                for col, param in ED_VITALS.items():
                    x = vital(param, row.get(col))
                    if x is not None:
                        store.add(s, Event(s.pid, t, MEASUREMENT, param, x, source=f"ed/{name}"))
                        self.counts[f"vital:{param}"] += 1

    def labs(self, store: Store) -> None:
        items = {}
        for row in read_rows(self.table("d_labitems")):
            param = LAB_LABELS.get((row.get("label") or "").strip().lower())
            if param and (row.get("fluid") or "").strip().lower() == "blood":
                items[row["itemid"]] = param
        for row in self.rows("labevents"):
            param = items.get(row.get("itemid"))
            t = ts(row.get("charttime"))
            x = _num(row.get("valuenum"))
            if param is None or t is None or x is None:
                continue
            unit = (row.get("valueuom") or "").strip().lower()
            x = x * _MIMIC_FACTORS[(param, unit)] if (param, unit) in _MIMIC_FACTORS else _convert(param, x, unit)
            for s in self.stays_at(row["subject_id"], t, BASELINE_LOOKBACK):
                if t < s.intime and param not in BASELINE_PARAMS:
                    continue
                store.add(s, Event(s.pid, t, MEASUREMENT, param, x, source=f"labevents/{row.get('itemid')}"))
                self.counts[f"lab:{param}"] += 1

    def diagnoses(self, store: Store) -> None:
        disch = {hadm: d for rows in self.prior_admissions.values() for d, hadm in rows}
        for row in self.rows("diagnoses_icd", required=False):
            d = disch.get(row.get("hadm_id"))
            if d is None:
                continue
            code = (row.get("icd_code") or "").strip()
            codes = [code] if row.get("icd_version") == "10" else self.gem.get(code, [])
            if row.get("icd_version") == "9" and not codes:
                self.counts["dx:icd9_unmapped"] += 1
            for s in self.by_subject[row["subject_id"]]:
                if d >= s.intime:
                    continue  # coded at a discharge after this ED arrival
                for c in codes:
                    for f in registry.factors_for_icd10(c):
                        store.add(s, Event(s.pid, d, CONDITION, f.id, c, source=f"diagnoses_icd/{row['hadm_id']}"))
                        self.counts[f"dx:{f.id}"] += 1

    def ed_medications(self, store: Store) -> None:
        stay_index = {s.stay_id: s for s in self.stays.values()}
        for name in ("medrecon", "pyxis"):
            for row in self.rows(name, required=False):
                s = stay_index.get(row.get("stay_id"))
                t = ts(row.get("charttime"))
                text = (row.get("name") or "").strip()
                if s is None or t is None or not text or t > s.intime + FOLLOW_UP:
                    continue
                for f in registry.medication_classes(text):
                    store.add(s, Event(s.pid, t, MEDICATION, f.id, text, source=f"ed/{name}"))
                    self.counts[f"med:{f.id}"] += 1
                key = text.lower().split()[0]
                store.add(s, Event(s.pid, t, MEDICATION, f"rx:{key}", text, source=f"ed/{name}"))
                if name == "pyxis" and any(p in text.lower() for p in WARD_PRESSORS):
                    self._outcome(s, "vasopressor_start", t, text, store, "ed/pyxis")

    def _outcome(self, s: Stay, kind: str, t: datetime, value, store: Store, source: str) -> None:
        if not s.intime <= t <= s.intime + FOLLOW_UP:
            return
        s.note(kind, t)
        store.add(s, Event(s.pid, t, OUTCOME, kind, value, source=source))

    def outcomes(self, store: Store) -> None:
        first_unit = {}
        for row in self.rows("transfers"):
            t = ts(row.get("intime"))
            unit = (row.get("careunit") or "").strip()
            if t is None:
                continue
            if row.get("eventtype") == "admit":
                first_unit[row.get("hadm_id")] = unit
            if any(w in unit.lower() for w in ICU_UNIT_WORDS):
                for s in self.stays_at(row["subject_id"], t):
                    self._outcome(s, "icu_transfer", t, unit, store, "transfers")
        for s in self.stays.values():
            unit = first_unit.get(s.hadm, "")
            s.direct_icu = bool(s.hadm) and any(w in unit.lower() for w in ICU_UNIT_WORDS)
        for row in self.rows("procedureevents", required=False):
            t = ts(row.get("starttime"))
            if t and row.get("itemid") in INTUBATION_ITEMIDS:
                for s in self.stays_at(row["subject_id"], t):
                    self._outcome(s, "intubation", t, True, store, "procedureevents")
        for row in self.rows("inputevents", required=False):
            t = ts(row.get("starttime"))
            drug = PRESSOR_ITEMIDS.get(row.get("itemid"))
            if t and drug:
                for s in self.stays_at(row["subject_id"], t):
                    self._outcome(s, "vasopressor_start", t, drug, store, "inputevents")
        for row in self.rows("emar", required=False):
            med = (row.get("medication") or "").lower()
            t = ts(row.get("charttime"))
            if t and any(p in med for p in WARD_PRESSORS) and (row.get("event_txt") or "").lower() in EMAR_GIVEN:
                for s in self.stays_at(row["subject_id"], t):
                    self._outcome(s, "vasopressor_start", t, med, store, "emar")
        for s in self.stays.values():
            if s.death:
                self._outcome(s, "death", s.death, True, store, "admissions")

    def run(self, out_dir: str) -> Store:
        os.makedirs(out_dir, exist_ok=True)
        self.cohort()
        store = Store(os.path.join(out_dir, "events.sqlite"))
        self.demographics(store)
        self.ed_vitals(store)
        self.labs(store)
        self.diagnoses(store)
        self.ed_medications(store)
        self.outcomes(store)
        store.close()
        print(f"extracted {store.n} events", file=self.log)
        return store


# --- outputs --------------------------------------------------------------------

AUDIT_COLUMNS = ["record_id", "triage_time", "admitted", "excluded_reason", "icu_transfer_time", "rrt_time",
                 "intubation_time", "vasopressor_start_time", "death_time", "ed_revisit_72h"]


def write_audit(stays, path: str, exclude_direct_icu: bool = False) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, AUDIT_COLUMNS)
        w.writeheader()
        for s in stays:
            row = {"record_id": s.pid, "triage_time": s.intime.isoformat(), "admitted": "Y" if s.hadm else "N",
                   "excluded_reason": "direct ICU admission" if exclude_direct_icu and s.direct_icu else "",
                   "ed_revisit_72h": "Y" if s.revisit_72h else "N"}
            for kind, t in s.outcomes.items():
                row[f"{kind}_time"] = t.isoformat()
            w.writerow(row)


def compact(snap: dict) -> dict:
    """The parts of a snapshot uri.model.features() and uri.calibrate read."""
    sc = snap["scores"]
    keep = {k: {"total": v["total"]} for k, v in sc.items() if isinstance(v, dict) and "total" in v}
    keep["sofa"].update({k: sc["sofa"].get(k) for k in ("missing", "rise_48h", "acute")})
    return {"patient_id": snap["patient_id"], "time": snap["time"], "episode_start": snap.get("episode_start"),
            "uri": snap["uri"], "uri_v1": snap["uri_v1"], "scores": keep,
            "trajectory": {"points": snap["trajectory"]["points"]}, "context": snap.get("context", {}),
            "critical_values": [{"severity": c["severity"]} for c in snap.get("critical_values", [])],
            "risk_factors": [{"id": f["id"]} for f in snap.get("risk_factors", [])]}


def replay(store: Store, stays: dict[str, Stay], out_dir: str, cfg: Config | None = None,
           exclude: set[str] = frozenset(), log=None) -> dict:
    log = log or sys.stderr
    """Stream every stay through the engine, one patient at a time."""
    engine = Engine(cfg)
    gap = timedelta(hours=engine.cfg.episode_gap_hours)
    n = 0
    with open(os.path.join(out_dir, "snapshots.jsonl"), "w") as snaps_fh, \
            open(os.path.join(out_dir, "outcomes.csv"), "w") as out_fh, \
            open(os.path.join(out_dir, "encounters.csv"), "w") as enc_fh:
        out_fh.write("patient_id,time,type\n")
        enc_fh.write("patient_id,end_time\n")
        for pid, hist, now in store.patients():
            s = stays.get(pid)
            if s is None or pid in exclude:
                continue
            engine.preload(hist)
            hourly = {}
            batch = []
            for e in now + [None]:
                if batch and (e is None or e.time != batch[0].time):
                    snap = engine.ingest_many(batch).get(pid)
                    if snap is not None:
                        t = batch[0].time
                        if t <= s.intime + FOLLOW_UP:
                            hourly[int((t - s.intime) / H)] = compact(snap)
                    batch = []
                if e is not None:
                    batch.append(e)
            for snap in hourly.values():
                snaps_fh.write(json.dumps(snap) + "\n")
            state = engine.patients.get(pid)
            if state is not None:
                for kind, t in sorted(state.observed_outcomes(gap).items(), key=lambda x: x[1]):
                    out_fh.write(f"{pid},{t.isoformat()},{kind}\n")
            if s.end:
                enc_fh.write(f"{pid},{s.end.isoformat()}\n")
            engine.patients.pop(pid, None)
            n += 1
            if n % 5000 == 0:
                print(f"  replayed {n} stays", file=log)
    print(f"replayed {n} stays", file=log)
    return {"replayed": n}


def run(args) -> int:
    from . import audit
    ex = Extractor(args.mimic_dir, args.ed_dir, args.limit, args.seed, args.icd9_gem)
    store = ex.run(args.out)
    stays = sorted(ex.stays.values(), key=lambda s: s.intime)
    audit_path = os.path.join(args.out, "audit.csv")
    write_audit(stays, audit_path, args.exclude_direct_icu)
    with open(os.path.join(args.out, "extract_counts.json"), "w") as fh:
        json.dump(dict(sorted(ex.counts.items())), fh, indent=2)
    print("\n=== Phase 0 audit (MIMIC-IV-ED) ===")
    audit.main([audit_path])
    exclude = {s.pid for s in stays if args.exclude_direct_icu and s.direct_icu}
    cfg = Config.from_json(args.config) if args.config else None
    replay(store, ex.stays, args.out, cfg, exclude)
    try:
        from . import calibrate
    except ImportError:
        print("numpy not installed: skipping calibration", file=sys.stderr)
        return 0
    print("\n=== Calibration (landmark model vs NEWS2 and URI v1) ===")
    return calibrate.main(["--snapshots", os.path.join(args.out, "snapshots.jsonl"),
                           "--outcomes", os.path.join(args.out, "outcomes.csv"),
                           "--encounters", os.path.join(args.out, "encounters.csv"),
                           "--out", os.path.join(args.out, "model.json"), "--bootstrap", str(args.bootstrap)])


# --- synthetic tables in the MIMIC layout ------------------------------------------

def _write(path: str, header: list[str], rows: list[list]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    with gzip.open(path, "wt", newline="") as fh:
        fh.write(buf.getvalue())


def demo_tables(root: str) -> tuple[str, str]:
    """A handful of synthetic patients in the MIMIC-IV / MIMIC-IV-ED layout
    (shifted 2180s dates, as in MIMIC). Returns (mimic_dir, ed_dir)."""
    mimic, ed = os.path.join(root, "mimiciv"), os.path.join(root, "mimic-iv-ed")
    t0 = datetime(2180, 5, 6, 8, 0)

    def f(t):
        return t.strftime("%Y-%m-%d %H:%M:%S")

    def at(h):
        return f(t0 + timedelta(hours=h))

    _write(f"{ed}/ed/edstays.csv.gz",
           ["subject_id", "hadm_id", "stay_id", "intime", "outtime", "gender", "race", "arrival_transport",
            "disposition"],
           [[10001, 20001, 30001, at(0), at(5), "M", "WHITE", "AMBULANCE", "ADMITTED"],
            [10002, "", 30002, at(0), at(4), "F", "WHITE", "WALK IN", "HOME"],
            [10002, 20002, 30003, at(30), at(34), "F", "WHITE", "WALK IN", "ADMITTED"],
            [10003, 20003, 30004, at(0), at(3), "M", "WHITE", "AMBULANCE", "ADMITTED"],
            [10004, 20004, 30005, at(0), at(6), "F", "WHITE", "WALK IN", "ADMITTED"]])
    _write(f"{ed}/ed/triage.csv.gz",
           ["subject_id", "stay_id", "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain",
            "acuity", "chiefcomplaint"],
           [[10001, 30001, 100.4, 104, 22, 95, 108, 64, "4", 2, "FEVER"],
            [10002, 30002, 98.2, 76, 16, 99, 128, 80, "0", 4, "RASH"],
            [10002, 30003, 98.4, 80, 16, 98, 124, 78, "2", 3, "RASH"],
            [10003, 30004, 97.9, 118, 28, 89, 92, 58, "UTA", 1, "DYSPNEA"],
            [10004, 30005, 98.6, 88, 18, 97, 134, 82, "3", 3, "HYPERGLYCEMIA"]])
    _write(f"{ed}/ed/vitalsign.csv.gz",
           ["subject_id", "stay_id", "charttime", "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp",
            "rhythm", "pain"],
           [[10001, 30001, at(2), 101.8, 116, 26, 93, 94, 56, "", "4"],
            [10001, 30001, at(4), 102.2, 124, 30, 91, 84, 50, "", "5"],
            [10003, 30004, at(1), "", 124, 32, 87, 86, 54, "", ""],
            [10004, 30005, at(3), 98.6, 900, 18, 97, 130, 80, "", "2"]])  # HR 900: charting error
    _write(f"{ed}/ed/medrecon.csv.gz",
           ["subject_id", "stay_id", "charttime", "name", "gsn", "ndc", "etc_rn", "etccode", "etcdescription"],
           [[10001, 30001, at(1), "apixaban", "", "", 1, "", ""],
            [10004, 30005, at(1), "insulin glargine", "", "", 1, "", ""]])
    _write(f"{ed}/ed/pyxis.csv.gz", ["subject_id", "stay_id", "charttime", "med_rn", "name", "gsn_rn", "gsn"],
           [[10003, 30004, at(2), 1, "Norepinephrine", 1, ""],
            [10001, 30001, at(1), 1, "Acetaminophen", 1, ""]])
    _write(f"{mimic}/hosp/patients.csv.gz",
           ["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group", "dod"],
           [[10001, "M", 70, 2178, "2014 - 2016", ""], [10002, "F", 40, 2180, "2017 - 2019", ""],
            [10003, "M", 81, 2180, "2017 - 2019", "2180-05-07"], [10004, "F", 33, 2179, "2011 - 2013", ""]])
    _write(f"{mimic}/hosp/admissions.csv.gz",
           ["subject_id", "hadm_id", "admittime", "dischtime", "deathtime", "admission_type"],
           [[10001, 20000, at(-24 * 200), at(-24 * 195), "", "EW EMER."],
            [10001, 20001, at(5), at(24 * 6), "", "EW EMER."],
            [10002, 20002, at(34), at(60), "", "EW EMER."],
            [10003, 20003, at(3), at(40), at(40), "EW EMER."],
            [10004, 20009, at(-24 * 400), at(-24 * 397), "", "EW EMER."],
            [10004, 20004, at(6), at(24 * 3), "", "EW EMER."]])
    _write(f"{mimic}/hosp/d_labitems.csv.gz", ["itemid", "label", "fluid", "category"],
           [[50813, "Lactate", "Blood", "Blood Gas"], [50912, "Creatinine", "Blood", "Chemistry"],
            [51081, "Creatinine", "Urine", "Chemistry"], [51222, "Hemoglobin", "Blood", "Hematology"],
            [51301, "White Blood Cells", "Blood", "Hematology"], [51003, "Troponin T", "Blood", "Chemistry"],
            [50931, "Glucose", "Blood", "Chemistry"]])
    lab_cols = ["labevent_id", "subject_id", "hadm_id", "specimen_id", "itemid", "order_provider_id", "charttime",
                "storetime", "value", "valuenum", "valueuom", "ref_range_lower", "ref_range_upper", "flag",
                "priority", "comments"]
    labs = [(10001, 50912, -24 * 150, 2.0, "mg/dL"), (10001, 51222, -24 * 150, 13.1, "g/dL"),
            (10001, 50931, -24 * 150, 140, "mg/dL"),  # not a baseline parameter: dropped
            (10001, 50813, 1, 3.1, "mmol/L"), (10001, 50912, 1, 2.6, "mg/dL"), (10001, 51301, 1, 17.2, "K/uL"),
            (10001, 50813, 4, 4.6, "mmol/L"), (10001, 51081, 1, 80, "mg/dL"),
            (10003, 50813, 1, 5.2, "mmol/L"), (10003, 51003, 1, 0.42, "ng/mL"),
            (10004, 50931, 1, 412, "mg/dL"), (10002, 51222, 31, 12.8, "g/dL")]
    _write(f"{mimic}/hosp/labevents.csv.gz", lab_cols,
           [[i, s, "", i, item, "", at(h), at(h), v, v, u, "", "", "", "", ""]
            for i, (s, item, h, v, u) in enumerate(labs, 1)])
    _write(f"{mimic}/hosp/diagnoses_icd.csv.gz", ["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version"],
           [[10001, 20000, 1, "N184", 10], [10001, 20000, 2, "I5022", 10],
            [10001, 20001, 1, "A419", 10],  # index admission: coded at discharge, must not leak
            [10004, 20009, 1, "25001", 9]])
    _write(f"{mimic}/hosp/transfers.csv.gz",
           ["subject_id", "hadm_id", "transfer_id", "eventtype", "careunit", "intime", "outtime"],
           [[10001, 20001, 1, "ED", "Emergency Department", at(0), at(5)],
            [10001, 20001, 2, "admit", "Medicine", at(5), at(10)],
            [10001, 20001, 3, "transfer", "Medical Intensive Care Unit (MICU)", at(10), at(60)],
            [10003, 20003, 4, "ED", "Emergency Department", at(0), at(3)],
            [10003, 20003, 5, "admit", "Coronary Care Unit (CCU)", at(3), at(40)],
            [10004, 20004, 6, "admit", "Medicine", at(6), at(72)]])
    _write(f"{mimic}/hosp/emar.csv.gz",
           ["subject_id", "hadm_id", "emar_id", "emar_seq_num", "poe_id", "pharmacy_id", "enter_provider_id",
            "charttime", "medication", "event_txt", "scheduletime", "storetime"],
           [[10004, 20004, "e1", 1, "", "", "", at(8), "Phenylephrine 2.5% Ophth Soln", "Administered", "", ""]])
    _write(f"{mimic}/icu/procedureevents.csv.gz",
           ["subject_id", "hadm_id", "stay_id", "caregiver_id", "starttime", "endtime", "storetime", "itemid",
            "value"],
           [[10001, 20001, 40001, "", at(11), at(11), "", 224385, 1]])
    _write(f"{mimic}/icu/inputevents.csv.gz",
           ["subject_id", "hadm_id", "stay_id", "caregiver_id", "starttime", "endtime", "storetime", "itemid",
            "amount"],
           [[10001, 20001, 40001, "", at(10.5), at(20), "", 221906, 4]])
    return mimic, ed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uri.mimic", description="MIMIC-IV-ED pipeline for URI")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract, Phase 0 audit, replay and calibrate")
    r.add_argument("--mimic-dir", required=True, help="MIMIC-IV root (contains hosp/ and icu/)")
    r.add_argument("--ed-dir", help="MIMIC-IV-ED root (contains ed/), if downloaded separately")
    r.add_argument("--out", default="data/mimic", help="output directory (keep it under the git-ignored data/)")
    r.add_argument("--limit", type=int, help="random sample of this many ED stays")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--icd9-gem", help="CMS ICD-9 -> ICD-10 GEM file (2018_I9gem.txt) for pre-2015 diagnoses")
    r.add_argument("--exclude-direct-icu", action="store_true",
                   help="exclude stays admitted from the ED straight to an ICU (planned ICU admissions)")
    r.add_argument("--config", help="JSON config overriding engine defaults")
    r.add_argument("--bootstrap", type=int, default=200)
    d = sub.add_parser("demo-data", help="write synthetic tables in the MIMIC layout")
    d.add_argument("dir")
    args = p.parse_args(argv)
    if args.cmd == "demo-data":
        mimic, ed = demo_tables(args.dir)
        print(f"python -m uri.mimic run --mimic-dir {mimic} --ed-dir {ed} --out {args.dir}/out --bootstrap 0")
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
