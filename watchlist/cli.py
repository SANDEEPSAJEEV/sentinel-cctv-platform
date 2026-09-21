"""Watchlist and alerts from the command line.

    python -m watchlist.cli add GJ01DM4242 --severity critical --category stolen \
        --reason "reported stolen" --source-ref FIR/2026/0142
    python -m watchlist.cli load --csv watchlist/samples/representative.csv
    python -m watchlist.cli scan            # check every unseen read
    python -m watchlist.cli follow          # keep checking
    python -m watchlist.cli alerts
    python -m watchlist.cli ack 7 --by "PSI Patel" --note "verified, not the vehicle"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date

from watchlist.engine import open_alerts, scan, set_status
from watchlist.sources import CsvSource, ManualSource, WatchlistEntry, load

log = logging.getLogger("watchlist.cli")
STATE_FILE = "out/watchlist_cursor.json"


def _cursor() -> int:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return int(json.load(fh).get("after_id", 0))
    except Exception:
        return 0


def _save_cursor(after_id: int) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"after_id": after_id}, fh)


def _print_alerts(rows: list[dict]) -> None:
    if not rows:
        print("no open alerts")
        return
    print(f"{'ID':<5}{'PRI':<5}{'SEVERITY':<10}{'WANTED':<12}{'READ':<12}{'CAMERA':<15}"
          f"{'HITS':<6}{'STATUS':<14}WHEN")
    print("-" * 104)
    for a in rows:
        flag = " (verify)" if a["needs_verification"] else ""
        print(f"{a['id']:<5}{a['priority']:<5}{a['severity']:<10}{a['plate_wanted']:<12}"
              f"{a['plate_read']:<12}{(a['camera_code'] or '—'):<15}{a['hit_count']:<6}"
              f"{a['status'] + flag:<14}{a['last_seen_at']:%Y-%m-%d %H:%M}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sentinel watchlist and alerts")
    sub = ap.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add one registration to the watchlist")
    add.add_argument("plate")
    add.add_argument("--category", default="bolo",
                     choices=["stolen", "wanted", "suspect", "bolo", "permit", "test"])
    add.add_argument("--severity", default="medium",
                     choices=["critical", "high", "medium", "low"])
    add.add_argument("--reason")
    add.add_argument("--source-ref", help="FIR / DD / circulation number")
    add.add_argument("--valid-until", help="ISO date the entry expires")
    add.add_argument("--by", help="who added it")

    loader = sub.add_parser("load", help="load a CSV of entries")
    loader.add_argument("--csv", required=True)
    loader.add_argument("--by")

    scanner = sub.add_parser("scan", help="check unseen plate reads once")
    scanner.add_argument("--from-start", action="store_true",
                         help="re-check every read, ignoring the saved cursor")

    follow = sub.add_parser("follow", help="keep checking as reads arrive")
    follow.add_argument("--interval", type=float, default=5.0)

    listing = sub.add_parser("alerts", help="show alerts")
    listing.add_argument("--all", action="store_true", help="include closed ones")
    listing.add_argument("--limit", type=int, default=50)

    ack = sub.add_parser("ack", help="acknowledge, dismiss or escalate an alert")
    ack.add_argument("alert_id", type=int)
    ack.add_argument("--status", default="acknowledged",
                     choices=["acknowledged", "dismissed", "escalated", "new"])
    ack.add_argument("--by")
    ack.add_argument("--note")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "add":
        entry = WatchlistEntry(
            plate=args.plate, category=args.category, severity=args.severity,
            reason=args.reason, source_ref=args.source_ref, added_by=args.by,
            valid_until=date.fromisoformat(args.valid_until) if args.valid_until else None)
        print(f"{load(ManualSource([entry]), added_by=args.by)} entry written "
              f"({entry.normalised()})")

    elif args.command == "load":
        with open(args.csv, encoding="utf-8-sig") as fh:
            n = load(CsvSource(fh.read()), added_by=args.by)
        print(f"{n} entries loaded from {args.csv}")

    elif args.command == "scan":
        after = 0 if args.from_start else _cursor()
        alerts, last = scan(after)
        _save_cursor(last)
        for a in alerts:
            kind = "repeat" if a.deduplicated else "NEW"
            print(f"[{kind}] alert {a.alert_id}: {a.plate_wanted} seen as {a.plate_read} "
                  f"on {a.camera_code} — {a.severity}, priority {a.priority}"
                  f"{', needs verification' if a.needs_verification else ''}")
        print(f"{len(alerts)} alert event(s); cursor now {last}")

    elif args.command == "follow":
        after = _cursor()
        print(f"following from read id {after}; ctrl-c to stop")
        try:
            while True:
                alerts, after = scan(after)
                for a in alerts:
                    log.warning("ALERT %s: %s seen as %s on %s (priority %d)%s",
                                a.alert_id, a.plate_wanted, a.plate_read, a.camera_code,
                                a.priority, " — needs verification" if a.needs_verification else "")
                _save_cursor(after)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nstopped")

    elif args.command == "alerts":
        _print_alerts(open_alerts(limit=args.limit, include_closed=args.all))

    elif args.command == "ack":
        ok = set_status(args.alert_id, args.status, by=args.by, note=args.note)
        print(f"alert {args.alert_id} -> {args.status}" if ok
              else f"no alert {args.alert_id}")


if __name__ == "__main__":
    main()
