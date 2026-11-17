"""Synthetic nightly job: aggregate today's sales into out/report-<RUN_DATE>.json.

--fault injects a realistic bug so Automation Doctor has something to catch:
  date_format      builds the filename with %Y-%d-%m (day/month swapped) and still exits 0
  early_exit       decides there is "no new data" and exits 0 without writing anything
  swallowed_error  a section crashes, the exception is logged and swallowed, the job exits 0
"""
import argparse
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

SALES = [{"region": "north", "amount": 120.0}, {"region": "south", "amount": 80.5}, {"region": "west", "amount": 42.0}]


def region_breakdown(fault: str) -> dict:
    if fault == "swallowed_error":
        raise KeyError("region_code")  # simulated schema change upstream
    return {s["region"]: s["amount"] for s in SALES}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", default="none", choices=["none", "date_format", "early_exit", "swallowed_error"])
    args = ap.parse_args()
    run_date = dt.date.fromisoformat(os.environ["RUN_DATE"])
    print(f"export_report: building report for {run_date}")

    if args.fault == "early_exit":
        print("export_report: watermark unchanged, no new data; nothing to do")
        return 0

    report = {"date": run_date.isoformat(), "total": round(sum(s["amount"] for s in SALES), 2),
              "orders": len(SALES)}
    try:
        report["regions"] = region_breakdown(args.fault)
    except Exception:
        traceback.print_exc()
        print("export_report: ERROR building region breakdown, continuing", file=sys.stderr)

    fmt = "%Y-%d-%m" if args.fault == "date_format" else "%Y-%m-%d"
    out = Path("out") / f"report-{run_date.strftime(fmt)}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"export_report: wrote {out} ({report['orders']} orders, total {report['total']})")
    print("export_report: SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
