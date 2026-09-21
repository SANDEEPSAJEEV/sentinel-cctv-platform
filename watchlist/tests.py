"""Checks for watchlist prioritisation that need no database.

    python -m watchlist.tests
"""

from __future__ import annotations

from datetime import date

from anpr.tests import check
from watchlist.engine import score_priority
from watchlist.sources import CsvSource, VahanSource, WatchlistEntry


def test_priority_ordering() -> None:
    print("alert priority")
    strong, _ = score_priority(severity="critical", match_kind="exact", usable=True,
                               capability_grade="IDENTIFY", plate_width_px=140)
    weak, _ = score_priority(severity="critical", match_kind="fuzzy", usable=False,
                             capability_grade="UNGRADED", plate_width_px=30)
    check("strong evidence outranks weak on the same severity", strong > weak,
          f"{strong} vs {weak}")

    critical_weak, _ = score_priority(severity="critical", match_kind="fuzzy", usable=False,
                                      capability_grade="DETECT", plate_width_px=30)
    low_strong, _ = score_priority(severity="low", match_kind="exact", usable=True,
                                   capability_grade="IDENTIFY", plate_width_px=140)
    check("a critical lead still outranks a low-severity certainty",
          critical_weak > low_strong, f"{critical_weak} vs {low_strong}")

    check("priority stays in range", all(1 <= p <= 100 for p in (strong, weak, low_strong)))


def test_priority_explains_itself() -> None:
    print("priority reasoning")
    _, why = score_priority(severity="high", match_kind="fuzzy", usable=False,
                            capability_grade="DETECT", plate_width_px=28)
    for fragment in ("high severity", "fuzzy match", "uncorroborated", "DETECT", "px wide"):
        check(f"reason mentions {fragment}", fragment in why, why)


def test_normalisation() -> None:
    print("entry normalisation")
    check("separators stripped", WatchlistEntry(plate="gj-01 dm 4242").normalised() == "GJ01DM4242")
    check("OCR-shaped typo repaired",
          WatchlistEntry(plate="GJO1DM4242").normalised() == "GJ01DM4242")


def test_csv_source() -> None:
    print("CSV source")
    rows = list(CsvSource(
        "plate,category,severity,source_ref,valid_until\n"
        "GJ01AB1234,stolen,critical,FIR/1,2026-12-31\n"
        ",,,,\n"
        "GJ02CD5678,bolo,low,BOLO/2,\n").fetch())
    check("two usable rows", len(rows) == 2, str(len(rows)))
    check("blank row skipped", all(r.plate for r in rows))
    check("dates parsed", rows[0].valid_until == date(2026, 12, 31))
    check("severity carried", rows[0].severity == "critical")


def test_government_adapters_refuse() -> None:
    print("government adapters")
    try:
        list(VahanSource().fetch())
        check("VAHAN refuses rather than inventing data", False)
    except NotImplementedError as exc:
        check("VAHAN refuses rather than inventing data", True)
        check("and says what it needs", "certificate" in str(exc).lower(), str(exc))


def main() -> None:
    for fn in (test_priority_ordering, test_priority_explains_itself, test_normalisation,
               test_csv_source, test_government_adapters_refuse):
        fn()
    print("\nall watchlist checks passed")


if __name__ == "__main__":
    main()
