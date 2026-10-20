"""Postgres access (Supabase). One short-lived connection per operation.

A direct Postgres connection is used instead of the Supabase REST client because a save
must be all-or-nothing, and batched REST calls cannot be wrapped in one transaction.
"""
from __future__ import annotations

import pandas as pd
import psycopg

from parsing import DB_COLUMNS
from screening import LIMIT_TOLERANCE, PRICE_LIMIT_PCT, SCREEN_SQL

COLS_SQL = ", ".join(DB_COLUMNS)
UPDATE_SQL = ", ".join(f"{c} = excluded.{c}" for c in DB_COLUMNS if c not in ("code", "date"))


def connect(url: str) -> psycopg.Connection:
    # prepare_threshold=None: Supabase's transaction pooler does not support prepared statements.
    return psycopg.connect(url, connect_timeout=15, prepare_threshold=None)


def _f(v):
    return None if v is None or pd.isna(v) else float(v)


def _i(v):
    return None if v is None or pd.isna(v) else int(v)


def _rows(df: pd.DataFrame):
    for r in df.itertuples(index=False):
        yield (r.code, r.date, _f(r.close), _f(r.adj_close), _f(r.change_pct),
               _i(r.volume), _f(r.value), _f(r.open), _f(r.high), _f(r.low))


def preview(url: str, df: pd.DataFrame) -> dict:
    """How many rows of df are new, and how many already exist (and would be refreshed)."""
    with connect(url) as conn:
        existing = conn.execute(
            "select code, date from prices where date between %s and %s",
            (df["date"].min(), df["date"].max()),
        ).fetchall()
    keys = set(existing)
    already = sum((c, d) in keys for c, d in zip(df["code"], df["date"]))
    return {"rows": len(df), "new": len(df) - already, "existing": already}


def save(url: str, df: pd.DataFrame, filenames: list[str]) -> dict:
    """Upsert on (code, date) and write the upload log, in a single transaction."""
    df = df.drop_duplicates(["code", "date"], keep="last")
    with connect(url) as conn:  # commits on success, rolls back if anything raises
        with conn.cursor() as cur:
            cur.execute("create temp table staging (like prices including defaults) on commit drop")
            with cur.copy(f"copy staging ({COLS_SQL}) from stdin") as copy:
                for row in _rows(df):
                    copy.write_row(row)
            cur.execute(
                f"""
                insert into prices ({COLS_SQL})
                select {COLS_SQL} from staging
                on conflict (code, date) do update set {UPDATE_SQL}
                returning (xmax = 0) as inserted  -- true for new rows, false for updated rows
                """
            )
            flags = [inserted for (inserted,) in cur.fetchall()]
            new = sum(flags)
            updated = len(flags) - new
            cur.execute(
                """
                insert into upload_log (filenames, rows_in_file, rows_new, rows_updated, date_min, date_max)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (", ".join(filenames), len(df), new, updated, df["date"].min(), df["date"].max()),
            )
    return {"rows": len(df), "new": new, "updated": updated}


def status(url: str) -> dict:
    with connect(url) as conn:
        rows, stocks, dmin, dmax = conn.execute(
            "select count(*), count(distinct code), min(date), max(date) from prices"
        ).fetchone()
        last = conn.execute(
            "select uploaded_at, filenames from upload_log order by id desc limit 1"
        ).fetchone()
    return {"rows": rows, "stocks": stocks, "date_min": dmin, "date_max": dmax,
            "last_upload": last[0] if last else None, "last_files": last[1] if last else None}


def trading_dates(url: str) -> list:
    with connect(url) as conn:
        return [d for (d,) in conn.execute("select distinct date from prices order by date").fetchall()]


def run_screen(url: str, start, end, threshold: int, min_value: float, min_price: float,
               code: str | None = None) -> pd.DataFrame:
    params = {"start": start, "end": end, "thr": threshold, "cap": PRICE_LIMIT_PCT + LIMIT_TOLERANCE,
              "min_value": min_value, "min_price": min_price, "code": code}
    with connect(url) as conn:
        cur = conn.execute(SCREEN_SQL, params)
        columns = [c.name for c in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


def count_rows(url: str, start=None, end=None) -> int:
    with connect(url) as conn:
        if start is None:
            return conn.execute("select count(*) from prices").fetchone()[0]
        return conn.execute(
            "select count(*) from prices where date between %s and %s", (start, end)
        ).fetchone()[0]


def delete_range(url: str, start, end) -> int:
    """Delete price rows between two dates (inclusive). The upload history is kept."""
    with connect(url) as conn:
        return conn.execute(
            "delete from prices where date between %s and %s", (start, end)
        ).rowcount


def delete_all(url: str) -> int:
    """Remove every price row and the upload history. Returns the number of price rows removed."""
    with connect(url) as conn:
        n = conn.execute("select count(*) from prices").fetchone()[0]
        conn.execute("truncate table prices, upload_log restart identity")
    return n


def trading_codes(url: str) -> list[str]:
    with connect(url) as conn:
        return [c for (c,) in conn.execute("select distinct code from prices order by code").fetchall()]


def period_changes(url: str, codes: list[str], start, end) -> pd.DataFrame:
    """Closes and reported daily changes of the given stocks, for screening.period_change()."""
    with connect(url) as conn:
        cur = conn.execute(
            "select code, date, close, change_pct from prices "
            "where code = any(%s) and date between %s and %s order by code, date",
            (list(codes), start, end),
        )
        columns = [c.name for c in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=columns)
    df["close"] = df["close"].astype(float)
    df["change_pct"] = df["change_pct"].astype(float)
    return df


def stock_history(url: str, code: str, start, end) -> pd.DataFrame:
    with connect(url) as conn:
        cur = conn.execute(
            "select date, close, change_pct, volume, value from prices "
            "where code = %s and date between %s and %s order by date",
            (code, start, end),
        )
        columns = [c.name for c in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=columns)
    for col in ("close", "change_pct", "value"):
        df[col] = df[col].astype(float)
    return df