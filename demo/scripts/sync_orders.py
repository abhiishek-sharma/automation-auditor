"""Synthetic job: fetch today's orders from an HTTP API and save them to out/orders-<RUN_DATE>.json.

The bug is that it trusts the HTTP status: any 200 counts as success, even an empty payload.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()
    with urllib.request.urlopen(args.url, timeout=10) as resp:
        status = resp.status
        data = json.loads(resp.read())
    items = data.get("items", [])
    out = Path("out") / f"orders-{os.environ['RUN_DATE']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(items))
    print(f"sync_orders: GET {args.url.split('?')[0]} -> HTTP {status}")
    print(f"sync_orders: saved {len(items)} orders to {out}")
    if status == 200:
        print("sync_orders: SUCCESS")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
