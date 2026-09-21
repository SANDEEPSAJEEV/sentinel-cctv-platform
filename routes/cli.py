"""Route reconstruction from the command line — the test-case answer.

    python -m routes.cli GJ01DM4242 --purpose FIR/2026/0142
    python -m routes.cli GJ01DM4242 --since 2026-09-21T06:00 --json out/route.json

On the day, the registration is handed over and this is what answers it: a
timestamped, location-wise route across the integrated cameras, with the
evidence behind every sighting and an honest note where the evidence is thin.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from routes.reconstruct import format_report, reconstruct


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.astimezone()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconstruct a vehicle's route")
    ap.add_argument("plate", help="registration to trace, e.g. GJ01DM4242")
    ap.add_argument("--since", help="ISO timestamp lower bound")
    ap.add_argument("--until", help="ISO timestamp upper bound")
    ap.add_argument("--tolerance", type=float, default=1.0,
                    help="fuzzy-match tolerance; 0 demands an exact read")
    ap.add_argument("--no-fold", action="store_true",
                    help="show every pass, including loop repeats")
    ap.add_argument("--corroborated-only", action="store_true",
                    help="drop sightings that no second frame confirmed")
    ap.add_argument("--purpose", help="FIR/DD reference this query is made under")
    ap.add_argument("--json", metavar="PATH", help="also write the full route as JSON")
    args = ap.parse_args()

    route = reconstruct(
        args.plate,
        since=_when(args.since), until=_when(args.until),
        tolerance=args.tolerance, fold=not args.no_fold,
        include_uncorroborated=not args.corroborated_only,
        purpose_ref=args.purpose,
    )
    print(format_report(route))

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(route.as_dict(), fh, indent=2)
        print(f"\nwritten: {args.json}")

    if not route.sightings:
        print("\nNo sighting of this registration. That is an answer: it means no "
              "camera in the registry read this plate in the window, which on this "
              "grid is as often about what the cameras can resolve as about where "
              "the vehicle went.")


if __name__ == "__main__":
    main()
