"""Recheck saved live cards using today's independent assertions, without network.

python -m evals.recheck data/evaluations/<timestamp>-live
Does not rewrite original observations or pretend to re-extract changed pages.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.live import violations_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    definitions = json.loads(Path(__file__).with_name("live_queries.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in definitions}
    checks = []
    for path in sorted(args.directory.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or "case" not in report or "events" not in report:
            continue
        case = cases.get(report["case"]["id"], report["case"])
        products = (report.get("final") or {}).get("products", [])
        problems = violations_for(case, products)
        for event in report["events"]:
            if event["event"] == "product":
                problems.extend(violations_for(case, [event["payload"]["product"]]))
        checks.append({"file": path.name, "violations": sorted(set(problems))})
    if not checks:
        parser.error("No live report files found in this directory")
    output = args.directory / ("recheck-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    output.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in checks:
        print(check["file"], check["violations"] or "PASS")
    print("Report:", output)
    return 1 if any(check["violations"] for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
