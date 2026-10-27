import contextlib
import csv
import io
import os
import sqlite3
import tempfile
import unittest

from uri import mimic


class MimicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mimic_dir, cls.ed_dir = mimic.demo_tables(cls.tmp.name)
        cls.gem = os.path.join(cls.tmp.name, "gem.txt")
        with open(cls.gem, "w") as fh:
            fh.write("25001 E1065 00000\n25001 E109 00000\n")
        cls.out = os.path.join(cls.tmp.name, "out")
        ex = mimic.Extractor(cls.mimic_dir, cls.ed_dir, gem_path=cls.gem, log=io.StringIO())
        cls.store = ex.run(cls.out)
        cls.stays = ex.stays
        cls.counts = ex.counts
        db = sqlite3.connect(os.path.join(cls.out, "events.sqlite"))
        cls.rows = db.execute("SELECT pid, t, hist, kind, code, value, source FROM ev").fetchall()

    @classmethod
    def tearDownClass(cls):
        cls.store.db.close()
        cls.tmp.cleanup()

    def codes(self, pid, kind=None):
        return {r[4] for r in self.rows if r[0] == pid and (kind is None or r[3] == kind)}

    def test_prior_diagnoses_only(self):
        # CKD and CHF from the admission 195 days earlier; the index admission's
        # sepsis code is assigned at discharge and must not leak in
        self.assertTrue({"ckd", "chf", "cci_renal"} <= self.codes("10001-30001", "condition"))
        self.assertFalse(any(r[5] == '"A419"' for r in self.rows))

    def test_icd9_through_gem(self):
        self.assertIn("dm1", self.codes("10004-30005", "condition"))

    def test_baseline_history_and_filters(self):
        hist = [(r[4], r[5]) for r in self.rows if r[0] == "10001-30001" and r[2] and r[3] == "measurement"]
        self.assertEqual(sorted(hist), [("creatinine", "2.0"), ("hgb", "13.1")])  # old glucose dropped
        vals = [float(r[5]) for r in self.rows if r[4] == "hr"]
        self.assertNotIn(900.0, vals)  # charting error
        self.assertEqual(sum(1 for r in self.rows if r[0] == "10001-30001" and r[4] == "creatinine"), 2)  # no urine
        temps = [float(r[5]) for r in self.rows if r[0] == "10001-30001" and r[4] == "temp_c"]
        self.assertEqual(temps[0], 38.0)  # 100.4 F

    def test_non_blood_labs_and_errors(self):
        vals = {(r[0], r[4]): r[5] for r in self.rows if r[3] == "measurement"}
        self.assertEqual(vals[("10004-30005", "urine_ketones")], "80.0")
        self.assertEqual(vals[("10001-30001", "urine_wbc")], "50.0")  # "21-50" -> upper end
        self.assertNotIn(("10002-30002", "creatinine"), vals)  # 0 mg/dL dropped
        ex_counts = self.counts
        self.assertEqual(ex_counts["lab_implausible:creatinine"], 1)
        self.assertEqual(ex_counts["lab_unmapped_nonblood:Creatinine | Urine"], 1)
        self.assertEqual(ex_counts["vital_implausible:hr"], 1)

    def test_outcomes(self):
        s = self.stays["10001-30001"]
        self.assertEqual(sorted(s.outcomes), ["icu_transfer", "intubation", "vasopressor_start"])
        self.assertEqual(s.outcomes["vasopressor_start"], s.intime + 3 * mimic.H)  # ED epinephrine
        self.assertEqual(self.stays["10002-30002"].outcomes, {})  # lidocaine with epinephrine
        s3 = self.stays["10003-30004"]
        self.assertTrue(s3.direct_icu)
        self.assertEqual(sorted(s3.outcomes), ["death", "icu_transfer", "vasopressor_start"])
        self.assertEqual(self.stays["10004-30005"].outcomes, {})  # ophthalmic phenylephrine is not a pressor
        self.assertTrue(self.stays["10002-30002"].revisit_72h)

    def test_audit_and_replay(self):
        path = os.path.join(self.out, "audit.csv")
        mimic.write_audit(sorted(self.stays.values(), key=lambda s: s.intime), path, exclude_direct_icu=True)
        with open(path, newline="") as fh:
            rows = {r["record_id"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows["10002-30002"]["admitted"], "N")
        self.assertEqual(rows["10003-30004"]["excluded_reason"], "direct ICU admission")
        mimic.replay(self.store, self.stays, self.out, log=io.StringIO())
        with open(os.path.join(self.out, "outcomes.csv")) as fh:
            outcomes = {(r["patient_id"], r["type"]) for r in csv.DictReader(fh)}
        self.assertIn(("10001-30001", "icu_transfer"), outcomes)
        with open(os.path.join(self.out, "snapshots.jsonl")) as fh:
            first = next(fh)
        self.assertIn('"charlson"', first)

    def test_compact_snapshot_feeds_the_model(self):
        import json

        from uri.model import FEATURES, features
        with open(os.path.join(self.out, "snapshots.jsonl")) as fh:
            snap = json.loads(next(fh))
        self.assertEqual(set(features(snap)), set(FEATURES))


class MimicRunTest(unittest.TestCase):
    def test_one_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, e = mimic.demo_tables(tmp)
            out = os.path.join(tmp, "out")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                mimic.main(["run", "--mimic-dir", m, "--ed-dir", e, "--out", out, "--bootstrap", "0"])
            text = buf.getvalue()
            # primary: the direct CCU admission is excluded; sensitivity: counted
            primary, sensitivity = text.split("Sensitivity (direct ICU admissions counted", 1)
            self.assertIn("Eligible admissions: 3   excluded: 2", primary)
            self.assertIn("Eligible admissions: 4   excluded: 1", sensitivity)
            self.assertTrue(os.path.exists(os.path.join(out, "snapshots.jsonl")))
            with open(os.path.join(out, "outcomes.csv")) as fh:
                self.assertNotIn("10003-30004", fh.read())
            with open(os.path.join(out, "sensitivity_direct_icu", "outcomes.csv")) as fh:
                self.assertIn("10003-30004", fh.read())
