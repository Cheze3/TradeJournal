"""Screener query and ranking. No database imports, so it can be tested without Postgres."""
from __future__ import annotations

import pandas as pd

PRICE_LIMIT_PCT = 7.0   # HOSE daily limit
LIMIT_TOLERANCE = 0.1   # rounding slack; moves beyond limit + tolerance are ignored
TOP_N = 20

# Prices are in thousand VND, traded value in billion VND (as exported by CafeF).
SCREEN_SQL = """
select
    code,
    sum(case when abs(change_pct) <= %(cap)s::numeric and change_pct >=  %(thr)s::numeric
             then 1 else 0 end) as up_days,
    sum(case when abs(change_pct) <= %(cap)s::numeric and change_pct <= -%(thr)s::numeric
             then 1 else 0 end) as down_days,
    sum(case when abs(change_pct) >  %(cap)s::numeric then 1 else 0 end) as ignored_days,
    count(*)     as days,
    avg(value)   as avg_value,
    avg(close)   as avg_close
from prices
where date between %(start)s::date and %(end)s::date
group by code
having coalesce(avg(value), 0) >= %(min_value)s::numeric
   and avg(close) >= %(min_price)s::numeric
"""

RANK_OPTIONS = {
    "Days up": "up_days",
    "Days down": "down_days",
    "Days up + days down": "moves",
}


def rank(raw: pd.DataFrame, rank_by: str, top: int = TOP_N) -> pd.DataFrame:
    """rank_by is a key of RANK_OPTIONS. Ties are listed alphabetically by code."""
    key = RANK_OPTIONS[rank_by]
    df = raw.copy()
    for col in ("up_days", "down_days", "ignored_days", "days"):
        df[col] = df[col].astype(int)
    for col in ("avg_value", "avg_close"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    df["moves"] = df["up_days"] + df["down_days"]
    df["up_share"] = (df["up_days"] / df["moves"] * 100).where(df["moves"] > 0)
    df = df[df[key] > 0]
    df = df.sort_values([key, "code"], ascending=[False, True])
    return df.head(top).reset_index(drop=True)