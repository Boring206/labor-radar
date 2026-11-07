"""uv run python -m labor_radar samples/lin_xiaomei.json [--lang zh-TW] [--json]"""

import argparse
import json
import sys
from pathlib import Path

from labor_radar import report
from labor_radar.engine import assess
from labor_radar.intake import IntakeError, parse_record


def main() -> int:
    parser = argparse.ArgumentParser(prog="labor_radar")
    parser.add_argument("record", type=Path)
    parser.add_argument("--lang", default="zh-TW")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args()
    try:
        record = parse_record(json.loads(args.record.read_text(encoding="utf-8")))
    except (IntakeError, json.JSONDecodeError) as error:
        print(f"input rejected: {error}", file=sys.stderr)
        return 2
    view = report.render(assess(record), args.lang)
    print(json.dumps(view, ensure_ascii=False, indent=2) if args.json else report.to_markdown(view))
    return 0


if __name__ == "__main__":
    sys.exit(main())
