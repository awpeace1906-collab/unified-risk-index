"""Command line: python -m uri {demo,simulate,replay,serve}"""

from __future__ import annotations

import argparse
import json
import sys

from . import simulate
from .config import Config
from .engine import Engine
from .events import Event
from .fhir import FhirAdapter


def _engine(args) -> tuple[Engine, FhirAdapter]:
    cfg = Config.from_json(args.config) if args.config else Config()
    cfg.local_codes = {**simulate.LOCAL_CODES, **cfg.local_codes}  # demo O2-device row
    sinks = []
    if args.log:
        fh = open(args.log, "a")
        sinks.append(lambda snap: (fh.write(json.dumps(snap) + "\n"), fh.flush()))
    return Engine(cfg, sinks), FhirAdapter(cfg.local_codes, icu_patterns=tuple(cfg.icu_location_patterns))


_printed_outcomes: set = set()


def _print_row(snap: dict) -> None:
    sig = ", ".join(s["id"] for s in snap["trajectory"]["signals"]) or "-"
    causes = "; ".join(c["cause"] for c in snap["correctable_causes"][:2]) or "-"
    alerts = " ".join(f"[{a['type']}]" for a in snap["alerts"])
    new = [o for o in snap.get("outcomes", []) if (snap["patient_id"], o["type"]) not in _printed_outcomes]
    _printed_outcomes.update((snap["patient_id"], o["type"]) for o in new)
    outcomes = "".join(f" <outcome: {o['type']} {o['time'][11:16]}>" for o in new)
    print(f"{snap['time'][11:16]}  {snap['patient_id']:<16} URI {snap['uri']:5.1f} "
          f"({snap['band']:<12}) static {snap['uri_static']:5.1f} +traj {snap['trajectory']['added']:4.1f} "
          f"| v1 {snap['uri_v1']:5.1f} | SOFA {snap['scores']['sofa']['total']:g}  "
          f"signals: {sig}  causes: {causes} {alerts}{outcomes}")


def _run(engine: Engine, adapter: FhirAdapter, records) -> None:
    """Feed records through the engine. Consecutive records for the same
    patient and time are applied as one batch, the way an interface engine
    delivers a charted vitals set."""
    batch, key = [], None

    def flush():
        for snap in engine.ingest_many(batch).values():
            _print_row(snap)

    for rec in records:
        events = adapter.events(rec) if "resourceType" in rec else [Event.from_dict(rec)]
        if not events:
            continue
        k = (events[0].patient_id, events[0].time)
        if batch and k != key:
            flush()
            batch = []
        batch += events
        key = k
    if batch:
        flush()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uri", description="Unified Risk Index streaming engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "replay", "serve"):
        s = sub.add_parser(name)
        s.add_argument("--config", help="JSON config overriding defaults")
        s.add_argument("--log", help="append every snapshot as JSONL (shadow-mode log)")
        s.add_argument("--outcomes-out", help="write observed composite outcomes as CSV (input to uri.calibrate)")
    sub.choices["demo"].add_argument("--scenario", nargs="+", default=simulate.DEFAULT_SCENARIOS,
                                     choices=list(simulate.SCENARIOS))
    sub.choices["replay"].add_argument("file", help="NDJSON of FHIR resources or normalized events")
    sub.choices["serve"].add_argument("--host", default="127.0.0.1")
    sub.choices["serve"].add_argument("--port", type=int, default=8080)
    sim = sub.add_parser("simulate", help="write a synthetic FHIR NDJSON feed")
    sim.add_argument("--scenario", nargs="+", default=simulate.DEFAULT_SCENARIOS, choices=list(simulate.SCENARIOS))
    args = p.parse_args(argv)

    if args.cmd == "simulate":
        for r in simulate.stream(args.scenario):
            print(json.dumps(r))
        return 0

    engine, adapter = _engine(args)
    if args.cmd == "demo":
        _run(engine, adapter, simulate.stream(args.scenario))
    elif args.cmd == "replay":
        with open(args.file) as fh:
            _run(engine, adapter, (json.loads(line) for line in fh if line.strip()))
    elif args.cmd == "serve":
        from .server import serve
        httpd = serve(engine, adapter, args.host, args.port)
        print(f"listening on http://{args.host}:{args.port}", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    if getattr(args, "outcomes_out", None):
        with open(args.outcomes_out, "w") as fh:
            fh.write("patient_id,time,type\n")
            for pid, kind, t in engine.outcomes():
                fh.write(f"{pid},{t.isoformat()},{kind}\n")
    if adapter.skipped:
        print(f"skipped: {dict(adapter.skipped)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
