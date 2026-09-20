"""Database access for the Sentinel registry.

One connection helper, no ORM. The schema is the contract (registry/schema.sql)
and the queries are short enough to read.

DSN comes from SENTINEL_DB_DSN. The default points at the local sandbox
container on 55432 (a local Postgres already owns 5432 on this dev machine).
In deployment the DSN carries real credentials and comes from the environment
or a secret store — never from a file in this repo.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "postgresql://sentinel:sentinel_local_dev@127.0.0.1:55432/sentinel"

_DSN_CRED_RE = re.compile(r"(?<=//)[^/@\s]+:[^/@\s]+(?=@)")


def dsn() -> str:
    return os.environ.get("SENTINEL_DB_DSN", DEFAULT_DSN)


def safe_dsn(value: str | None = None) -> str:
    """DSN with the password stripped — the only form that may be logged."""
    return _DSN_CRED_RE.sub("***:***", value or dsn())


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


def query(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params: Any = None) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def audit(action: str, *, actor: str | None = None, object_type: str | None = None,
          object_id: str | None = None, purpose_ref: str | None = None,
          detail: dict[str, Any] | None = None) -> None:
    """Append to the audit log. Every query that touches camera data is
    purpose-bound: it carries the FIR/DD reference it was asked under."""
    import json

    execute(
        """INSERT INTO audit_log (actor, action, object_type, object_id, purpose_ref, detail)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (actor, action, object_type, object_id, purpose_ref,
         json.dumps(detail) if detail is not None else None),
    )
