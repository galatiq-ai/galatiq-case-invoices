"""Unit of work: one connection, one transaction, one bound event that queries bill."""

import contextlib
import time
from sqlite3 import Connection, Cursor, Row
from typing import Iterator

from . import db
from .wide_event import WideEvent


class UnitOfWork:
    def __init__(self, conn: Connection, event: WideEvent | None) -> None:
        self.conn = conn
        self.event = event

    def query(self, sql: str, params: tuple = ()) -> list[Row]:
        return self._run(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        return self._run(sql, params)

    def _run(self, sql: str, params: tuple) -> Cursor:
        start = time.perf_counter()
        cursor = self.conn.execute(sql, params)
        if self.event is not None:
            self.event.add_db_query((time.perf_counter() - start) * 1000)
        return cursor


@contextlib.contextmanager
def unit_of_work(
    event: WideEvent | None = None, *, immediate: bool = False
) -> Iterator[UnitOfWork]:
    conn = db.connect()
    if immediate:
        # Take the write lock up front
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield UnitOfWork(conn, event)
        conn.execute("COMMIT") if immediate else conn.commit()
    except Exception:
        conn.execute("ROLLBACK") if immediate else conn.rollback()
        raise
    finally:
        conn.close()
