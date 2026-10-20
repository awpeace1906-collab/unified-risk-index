"""Arrival-time history pull from the EHR's FHIR API.

A live feed (FHIR Subscription / interface engine) only carries what happens
from now on. When a patient first appears, this module queries the FHIR
server for what came before, so the engine starts with:

* 12 months of baseline labs (creatinine, hemoglobin, platelets, bilirubin,
  pCO2), from which uri.baseline derives the pre-episode baseline;
* vitals and labs from the last 48 h (clinic or transfer results), for trends;
* the problem list, active medications, encounters in the last 90 days,
  procedures in the last 30 days, demographics and advance directives.

Best effort by design: a failed or slow query is recorded in ``errors`` and
skipped. Scoring never waits on, or fails because of, history.

Authentication: a bearer token (e.g. from a SMART backend-services client),
supplied by the caller. Obtaining it is site-specific.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Callable

from . import fhir

BASELINE_LOINC = [code for code, param in fhir.LOINC_MAP.items()
                  if param in ("creatinine", "hgb", "plt", "bilirubin", "pco2")]

Fetch = Callable[[str, dict], dict]


def _http_fetch(timeout: float) -> Fetch:
    def fetch(url: str, headers: dict) -> dict:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    return fetch


class FhirHistory:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0,
                 lookback_days: int = 365, max_pages: int = 20, fetch: Fetch | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/fhir+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.lookback_days = lookback_days
        self.max_pages = max_pages
        self.fetch = fetch or _http_fetch(timeout)
        self.errors: list[str] = []

    def queries(self, patient_id: str, at: datetime) -> list[str]:
        def day(delta: timedelta) -> str:
            return (at - delta).date().isoformat()
        pid = urllib.parse.quote(patient_id, safe="")
        loinc = ",".join(f"{fhir.LOINC}|{c}" for c in BASELINE_LOINC)
        return [
            f"Patient/{pid}",
            f"Observation?patient={pid}&category=laboratory&code={urllib.parse.quote(loinc, safe='|,')}"
            f"&date=ge{day(timedelta(days=self.lookback_days))}&_count=200",
            f"Observation?patient={pid}&category=laboratory&date=ge{day(timedelta(hours=48))}&_count=200",
            f"Observation?patient={pid}&category=vital-signs&date=ge{day(timedelta(hours=48))}&_count=200",
            f"Condition?patient={pid}&_count=200",
            f"MedicationRequest?patient={pid}&status=active&_count=200",
            f"MedicationStatement?patient={pid}&status=active&_count=200",
            f"Encounter?patient={pid}&date=ge{day(timedelta(days=90))}&_count=100",
            f"Procedure?patient={pid}&date=ge{day(timedelta(days=30))}&_count=100",
            f"Consent?patient={pid}&_count=50",
        ]

    def resources(self, patient_id: str, at: datetime) -> list[dict]:
        """Every resource the queries return, following Bundle 'next' links."""
        out, seen = [], set()
        for query in self.queries(patient_id, at):
            url, pages = f"{self.base_url}/{query}", 0
            while url and pages < self.max_pages:
                try:
                    payload = self.fetch(url, self.headers)
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    self.errors.append(f"{query.split('?')[0]}: {exc}")
                    break
                pages += 1
                if payload.get("resourceType") == "Bundle":
                    items = [e["resource"] for e in payload.get("entry", []) if e.get("resource")]
                    url = next((link.get("url") for link in payload.get("link", [])
                                if link.get("relation") == "next"), None)
                else:
                    items, url = [payload], None
                for r in items:
                    key = (r.get("resourceType"), r.get("id"))
                    if r.get("resourceType") == "OperationOutcome" or (r.get("id") and key in seen):
                        continue
                    seen.add(key)
                    out.append(r)
        return out
