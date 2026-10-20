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
    print(f"{snap['time'][5:10]} {snap['time'][11:16]}  {snap['patient_id']:<16} URI {snap['uri']:5.1f} "
          f"({snap['band']:<12}) static {snap['uri_static']:5.1f} +traj {snap['trajectory']['added']:4.1f} "
          f"| v1 {snap['uri_v1']:5.1f} | SOFA {snap['scores']['sofa']['total']:g} "
          f"(acute {snap['scores']['sofa']['acute']:g})  "
          f"signals: {sig}  causes: {causes} {alerts}{outcomes}")


def _run(engine: Engine, adapter: FhirAdapter, records, sort: bool = True) -> None:
    """Feed records through the engine, one batch per (patient, time), the
    way an interface engine delivers a charted vitals set.

    With ``sort`` (the default) every record is converted first and the
    events are replayed in clinical-time order. FHIR bulk exports arrive
    grouped by resource type, not by time, and replaying them as-is would
    score each patient with their labs and vitals in the wrong order. Sorting
    holds the whole export in memory."""
    def flush(batch):
        for snap in engine.ingest_many(batch).values():
            _print_row(snap)

    def to_events(rec):
        return adapter.events(rec) if "resourceType" in rec else [Event.from_dict(rec)]

    if sort:
        events = sorted((e for rec in records for e in to_events(rec)), key=lambda e: e.time)
        groups = ([e] for e in events)
    else:
        groups = (to_events(rec) for rec in records)

    batch, key = [], None
    for events in groups:
        if not events:
            continue
        k = (events[0].patient_id, events[0].time)
        if batch and k != key:
            flush(batch)
            batch = []
        batch += events
        key = k
    if batch:
        flush(batch)


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
    sub.choices["replay"].add_argument("--as-is", action="store_true",
                                       help="replay in file order instead of clinical-time order")
    sub.choices["serve"].add_argument("--host", default="127.0.0.1")
    sub.choices["serve"].add_argument("--port", type=int, default=8080)
    sub.choices["serve"].add_argument("--history-url", help="FHIR base URL to pull each new patient's prior "
                                                            "history from (uri.history)")
    sub.choices["serve"].add_argument("--history-token-env", default="URI_FHIR_TOKEN",
                                      help="environment variable holding the bearer token for --history-url")
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
            _run(engine, adapter, (json.loads(line) for line in fh if line.strip()), sort=not args.as_is)
    elif args.cmd == "serve":
        import os

        from .history import FhirHistory
        from .server import serve
        history = None
        if args.history_url:
            history = FhirHistory(args.history_url, token=os.environ.get(args.history_token_env),
                                  lookback_days=engine.cfg.baseline_lookback_days)
        httpd = serve(engine, adapter, args.host, args.port, history)
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
