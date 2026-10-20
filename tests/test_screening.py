"""Run with: python tests/test_screening.py [path/to/cafef.xlsx]

The query is written for Postgres. Here it runs on SQLite (placeholders and casts are
translated) to check the counting logic. It does not prove Postgres behaviour.
"""
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsing import parse_cafef  # noqa: E402
from screening import (LIMIT_TOLERANCE, PRICE_LIMIT_PCT, SCREEN_SQL, period_change,  # noqa: E402
                       rank, summarize)

CAP = PRICE_LIMIT_PCT + LIMIT_TOLERANCE


def to_sqlite(sql: str) -> str:
    sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
    return re.sub(r"::\w+", "", sql)


def run(conn, start, end, thr, min_value=0.0, min_price=0.0, code=None) -> pd.DataFrame:
    params = {"start": start, "end": end, "thr": thr, "cap": CAP,
              "min_value": min_value, "min_price": min_price, "code": code}
    return pd.read_sql_query(to_sqlite(SCREEN_SQL), conn, params=params)


def make_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("create table prices (code text, date text, close real, change_pct real, value real)")
    conn.executemany("insert into prices values (?,?,?,?,?)", rows)
    return conn


ROWS = [
    # AAA: two up >=3, one down <=-3, one 2% move, one 20% (ignored)
    ("AAA", "2026-10-01", 10, 3.0, 5), ("AAA", "2026-10-02", 10, 4.5, 5),
    ("AAA", "2026-10-05", 10, -3.0, 5), ("AAA", "2026-10-06", 10, 2.0, 5),
    ("AAA", "2026-10-07", 10, 20.0, 5),
    # BBB: only down moves, low price and low value
    ("BBB", "2026-10-01", 2, -5.0, 0.5), ("BBB", "2026-10-02", 2, -3.5, 0.5),
    # CCC: no big moves
    ("CCC", "2026-10-01", 50, 1.0, 9), ("CCC", "2026-10-02", 50, -1.0, 9),
    # AAA outside the window
    ("AAA", "2026-11-01", 10, 6.0, 5),
]


def test_counts_and_ignored_days():
    r = run(make_db(ROWS), "2026-10-01", "2026-10-31", 3).set_index("code")
    assert (r.loc["AAA", "up_days"], r.loc["AAA", "down_days"]) == (2, 1)
    assert r.loc["AAA", "ignored_days"] == 1 and r.loc["AAA", "days"] == 5
    assert (r.loc["BBB", "up_days"], r.loc["BBB", "down_days"]) == (0, 2)
    assert (r.loc["CCC", "up_days"], r.loc["CCC", "down_days"]) == (0, 0)


def test_threshold_is_inclusive_and_cumulative():
    r = run(make_db(ROWS), "2026-10-01", "2026-10-31", 4).set_index("code")
    assert r.loc["AAA", "up_days"] == 1 and r.loc["AAA", "down_days"] == 0  # 3.0 no longer counts
    r1 = run(make_db(ROWS), "2026-10-01", "2026-10-31", 1).set_index("code")
    assert r1.loc["AAA", "up_days"] == 3 and r1.loc["CCC", "up_days"] == 1  # 1.0 counts at 1%


def test_filters():
    r = run(make_db(ROWS), "2026-10-01", "2026-10-31", 3, min_value=1.0)
    assert "BBB" not in set(r["code"])
    r = run(make_db(ROWS), "2026-10-01", "2026-10-31", 3, min_price=5.0)
    assert "BBB" not in set(r["code"])


def test_rank_orders_and_drops_zero():
    raw = run(make_db(ROWS), "2026-10-01", "2026-10-31", 3)
    up = rank(raw, "Days up")
    assert list(up["code"]) == ["AAA"]                       # BBB, CCC have 0 up days
    down = rank(raw, "Days down")
    assert list(down["code"]) == ["BBB", "AAA"]             # 2 then 1
    both = rank(raw, "Days up + days down")
    assert list(both["code"]) == ["AAA", "BBB"]             # 3 then 2
    assert round(up.loc[0, "up_share"], 1) == 66.7          # 2 up of 3 moves


def test_top_n_limit():
    rows = [(f"S{i:02d}", "2026-10-01", 10, 5.0, 5) for i in range(30)]
    raw = run(make_db(rows), "2026-10-01", "2026-10-01", 3)
    assert len(rank(raw, "Days up")) == 20


def test_code_search_bypasses_filters():
    conn = make_db(ROWS)
    # BBB trades 0.5 bn/day: excluded by a 1 bn filter, but a code search with no filter returns it
    assert "BBB" not in set(run(conn, "2026-10-01", "2026-10-31", 3, min_value=1.0)["code"])
    r = run(conn, "2026-10-01", "2026-10-31", 3, code="BBB")
    assert list(r["code"]) == ["BBB"] and int(r.loc[0, "down_days"]) == 2


def test_summarize_keeps_stock_without_big_moves():
    raw = run(make_db(ROWS), "2026-10-01", "2026-10-31", 3, code="CCC")
    s = summarize(raw)
    assert len(s) == 1 and s.loc[0, "moves"] == 0 and pd.isna(s.loc[0, "up_share"])


def test_period_change():
    rows = pd.DataFrame({
        "code": ["AAA"] * 3 + ["BBB"] * 3 + ["CCC"] * 3 + ["DDD"],
        "date": ["2026-10-01", "2026-10-02", "2026-10-05"] * 3 + ["2026-10-01"],
        "close": [100, 110, 99,        # AAA: first day excluded, +10% then -10%
                  100, 50, 55,         # BBB: bonus-share day (raw -50%, reported 0%), then +10%
                  10, 10, 10,          # CCC: reported +0.2% on flat closes is rounding noise
                  7],                  # DDD: one day only
        "change_pct": [5.0, 10.0, -10.0,
                       1.0, 0.0, 10.0,
                       0.2, 0.2, 0.2,
                       3.0],
    })
    out = period_change(rows)
    assert round(out["AAA"], 6) == -1.0
    assert round(out["BBB"], 6) == 10.0
    assert round(out["CCC"], 6) == 0.0
    assert "DDD" not in out.index and pd.isna(out.reindex(["DDD"])["DDD"])


def check_period_change(df, start="2026-07-01", end="2026-10-15"):
    """Without a corporate-action day the result must equal last close / first close - 1."""
    sub = df[(df["date"].astype(str) >= start) & (df["date"].astype(str) <= end)].sort_values(["code", "date"]).copy()
    sub["raw"] = sub.groupby("code")["close"].pct_change() * 100
    sub["event"] = (sub["change_pct"].astype(float) - sub["raw"]).abs() >= 1.0
    has_event = sub.groupby("code")["event"].any()
    mine = period_change(sub[["code", "date", "close", "change_pct"]])
    ratio = (sub.groupby("code")["close"].last() / sub.groupby("code")["close"].first() - 1) * 100
    plain = has_event[~has_event].index.intersection(mine.index)
    diff = (mine[plain] - ratio[plain]).abs()
    assert diff.max() < 1e-6, f"max diff {diff.max()}"
    ev = has_event[has_event].index.intersection(mine.index)
    print(f"period change: {len(plain)} stocks without corporate-action days match close ratio exactly; "
          f"{len(ev)} stocks with such days differ from the raw ratio by a median "
          f"{(mine[ev] - ratio[ev]).abs().median():.1f} points")


def check_real_file(path):
    df, _ = parse_cafef(path)
    conn = sqlite3.connect(":memory:")
    out = df[["code", "date", "close", "change_pct", "value"]].copy()
    out["date"] = out["date"].astype(str)
    out.to_sql("prices", conn, index=False)
    start, end, thr = "2026-01-01", "2026-10-15", 3
    sql = run(conn, start, end, thr).set_index("code")
    sub = df[(df["date"].astype(str) >= start) & (df["date"].astype(str) <= end)]
    sub = sub[sub["change_pct"].abs() <= CAP]
    up = sub[sub["change_pct"] >= thr].groupby("code").size()
    down = sub[sub["change_pct"] <= -thr].groupby("code").size()
    expect = pd.DataFrame({"up": up, "down": down}).fillna(0).astype(int)
    got = sql[["up_days", "down_days"]].rename(columns={"up_days": "up", "down_days": "down"})
    got = got.reindex(expect.index.union(got.index)).fillna(0).astype(int)
    expect = expect.reindex(got.index).fillna(0).astype(int)
    assert got.equals(expect), "SQL counts differ from the pandas calculation"
    check_period_change(df)
    print(f"real file ok: {len(got)} stocks, {int(got['up'].sum())} up days, {int(got['down'].sum())} down days")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    for p in sys.argv[1:]:
        check_real_file(p)