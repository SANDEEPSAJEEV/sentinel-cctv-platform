"""Where watchlist entries come from.

The problem statement asks for integration with VAHAN, SARTHI, eGujCop/CCTNS,
AFIS and NAFIS to be **designed and integration-ready**, not connected. So the
interface is real, the manual and CSV sources work, and the government adapters
are declared with the exact shape each would need — endpoint, authentication,
fields, refresh cadence, and what is still unknown.

A stub here raises NotImplementedError with what it needs. It never returns
fabricated data and never silently returns nothing: a watchlist source that
quietly yields an empty list is worse than one that refuses, because the
platform then reports "no hits" for a stolen vehicle it was never told about.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Protocol

from anpr.plates import clean, repair
from registry import db


@dataclass
class WatchlistEntry:
    plate: str
    category: str = "bolo"
    severity: str = "medium"
    reason: str | None = None
    source_system: str = "manual"
    source_ref: str | None = None
    added_by: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None

    def normalised(self) -> str:
        """Plates arrive written however the officer typed them."""
        return clean(repair(self.plate))


class WatchlistSource(Protocol):
    """A place watchlist entries come from."""

    name: str

    def fetch(self) -> Iterable[WatchlistEntry]:
        ...


@dataclass
class ManualSource:
    """Entries typed in by an operator, or seeded for a demonstration."""

    entries: list[WatchlistEntry] = field(default_factory=list)
    name: str = "manual"

    def fetch(self) -> Iterable[WatchlistEntry]:
        return list(self.entries)


@dataclass
class CsvSource:
    """A department's spreadsheet. Columns: plate, category, severity, reason,
    source_ref, valid_from, valid_until."""

    text: str
    name: str = "csv"

    def fetch(self) -> Iterable[WatchlistEntry]:
        for row in csv.DictReader(io.StringIO(self.text)):
            clean_row = {k.strip(): (v.strip() if isinstance(v, str) else v)
                         for k, v in row.items() if k}
            if not clean_row.get("plate"):
                continue
            yield WatchlistEntry(
                plate=clean_row["plate"],
                category=clean_row.get("category") or "bolo",
                severity=clean_row.get("severity") or "medium",
                reason=clean_row.get("reason") or None,
                source_system=clean_row.get("source_system") or "csv",
                source_ref=clean_row.get("source_ref") or None,
                valid_from=_as_date(clean_row.get("valid_from")),
                valid_until=_as_date(clean_row.get("valid_until")),
            )


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# ------------------------------------------------- government integrations
@dataclass
class VahanSource:
    """VAHAN — the national vehicle registration database.

    DESIGNED, NOT CONNECTED. What this adapter needs before it can run:

      endpoint      the state's VAHAN API gateway (NIC-issued, per-state)
      auth          NIC-issued client certificate plus a department API key;
                    requests are IP-allowlisted, so the platform's egress
                    addresses must be registered
      request       GET /vahan/registration/{registration_number}
      response      owner name, chassis, engine, make/model, colour, fuel,
                    registration and fitness validity, hypothecation, blacklist
                    and theft flags
      use here      the blacklist/theft flags become watchlist entries; the
                    make/model/colour become a cross-check against what the
                    camera saw, which is how a cloned plate is caught
      cadence       on-demand per hit, plus a nightly delta of flagged vehicles
      limits        per-department rate limits apply; responses are personal
                    data and must not be cached beyond the query's purpose

    Until those are issued, this refuses rather than inventing rows.
    """

    name: str = "vahan"

    def fetch(self) -> Iterable[WatchlistEntry]:
        raise NotImplementedError(
            "VAHAN adapter is designed, not connected: needs the state gateway URL, "
            "an NIC client certificate, a department API key and egress IP "
            "allowlisting. See the docstring for the request and response shape.")


@dataclass
class CctnsSource:
    """eGujCop / CCTNS — the crime and criminal tracking network.

    DESIGNED, NOT CONNECTED. What this adapter needs:

      endpoint      eGujCop integration bus, state data centre
      auth          department credentials over the GSWAN network; this is not
                    reachable from the public internet, so the platform must be
                    deployed inside GSWAN or given a controlled relay
      request       stolen-vehicle and wanted-vehicle circulations, by district
                    and date range
      response      FIR number, police station, circulation date, vehicle
                    particulars, officer contact
      use here      each circulation becomes a watchlist entry whose source_ref
                    is the FIR number, so every alert traces back to the case
                    that justifies it, and withdrawal of the circulation expires
                    the entry
      cadence       poll every 15 minutes; circulations are time-critical

    AFIS and NAFIS are fingerprint systems and carry no vehicle data; they are
    relevant to the person side of an investigation, not to this watchlist.
    """

    name: str = "cctns"

    def fetch(self) -> Iterable[WatchlistEntry]:
        raise NotImplementedError(
            "CCTNS/eGujCop adapter is designed, not connected: needs GSWAN network "
            "placement and department credentials. See the docstring for the "
            "circulation fields it would consume.")


# ------------------------------------------------------------------ loading
def load(source: WatchlistSource, *, added_by: str | None = None) -> int:
    """Upsert a source's entries. Returns the number written."""
    written = 0
    with db.connect() as conn, conn.cursor() as cur:
        for entry in source.fetch():
            plate = entry.normalised()
            if not plate:
                continue
            cur.execute(
                """INSERT INTO watchlist (plate, category, severity, reason,
                                          source_system, source_ref, added_by,
                                          valid_from, valid_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s,
                           coalesce(%s, current_date), %s)
                   ON CONFLICT (plate, source_ref) DO UPDATE SET
                       category = EXCLUDED.category,
                       severity = EXCLUDED.severity,
                       reason = EXCLUDED.reason,
                       valid_until = EXCLUDED.valid_until,
                       active = true""",
                (plate, entry.category, entry.severity, entry.reason,
                 entry.source_system or source.name, entry.source_ref,
                 entry.added_by or added_by, entry.valid_from, entry.valid_until),
            )
            written += 1
        conn.commit()
    db.audit("watchlist_load", actor=added_by or source.name,
             detail={"source": source.name, "entries": written})
    return written


AVAILABLE: dict[str, Any] = {
    "manual": ManualSource,
    "csv": CsvSource,
    "vahan": VahanSource,
    "cctns": CctnsSource,
}
