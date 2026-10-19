"""Minimal HTTP receiver for EMR pushes (FHIR Subscription / interface engine).

  POST /fhir                  FHIR resource or Bundle   -> updated snapshots
  POST /events                list of normalized events -> updated snapshots
  GET  /patients              latest URI for every patient, highest first
  GET  /patients/{id}         latest full snapshot
  GET  /patients/{id}/history URI time series

Standard library only. This has no authentication or TLS and is meant to sit
behind the institution's interface engine on a private network (shadow mode).
Do not expose it directly.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import Engine
from .events import Event
from .fhir import FhirAdapter


def make_handler(engine: Engine, adapter: FhirAdapter):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"null")
                if self.path == "/fhir":
                    events = adapter.events(payload)
                elif self.path == "/events":
                    events = [Event.from_dict(x) for x in payload]
                else:
                    return self._send(404, {"error": "not found"})
            except (ValueError, KeyError, TypeError) as exc:
                return self._send(400, {"error": str(exc)})
            with lock:
                snaps = engine.ingest_many(events)
            self._send(202, {"events": len(events), "snapshots": list(snaps.values())})

        def do_GET(self):
            parts = [p for p in self.path.split("/") if p]
            with lock:
                if parts == ["patients"]:
                    rows = [{"patient_id": pid, "uri": s.last["uri"], "band": s.last["band"],
                             "time": s.last["time"]}
                            for pid, s in engine.patients.items() if s.last]
                    return self._send(200, sorted(rows, key=lambda r: -r["uri"]))
                if len(parts) == 2 and parts[0] == "patients":
                    snap = engine.latest(parts[1])
                    return self._send(200, snap) if snap else self._send(404, {"error": "unknown patient"})
                if len(parts) == 3 and parts[0] == "patients" and parts[2] == "history":
                    return self._send(200, engine.history(parts[1]))
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):  # keep request bodies (PHI) out of logs
            pass

    return Handler


def serve(engine: Engine, adapter: FhirAdapter, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(engine, adapter))
