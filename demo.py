"""Handle the demo alert once against the local ops platform and print what happened.

    python demo.py
    python demo.py --quiet-world   # the same alert when nothing is actually wrong
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ops
from agent import IncidentAgent
from runtime import open_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=Path(".data") / "actions.jsonl")
    parser.add_argument("--quiet-world", action="store_true", help="no outage: the alert is noise")
    args = parser.parse_args()

    log = ops.ActionLog(args.log)
    log.path.unlink(missing_ok=True)  # one run per log keeps the demo readable

    ops_url, stop_ops = ops.serve_in_background(log, ops.World(outage=not args.quiet_world))
    model, close = open_model()
    try:
        report = IncidentAgent(model, ops_url).handle()
    finally:
        close()
        stop_ops()

    print(report)
    print(f"\nactions ({log.path}):")
    for entry in log.entries():
        print("  " + json.dumps(entry, sort_keys=True))


if __name__ == "__main__":
    main()
