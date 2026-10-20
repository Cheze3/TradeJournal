"""Screener query and ranking. No database imports, so it can be tested without Postgres."""
from __future__ import annotations

import pandas as pd

PRICE_LIMIT_PCT = 7.0   # HOSE daily limit
LIMIT_TOLERANCE = 0.1   # rounding slack; moves beyond limit + tolerance are ignored
TOP_N = 20
ACTION_GAP_PTS = 1.0   # reported vs close-to-close gap (points) that marks a corporate-action day

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
  and (%(code)s::text is null or code = %(code)s::text)
group by code
having coalesce(avg(value), 0) >= %(min_value)s::numeric
   and avg(close) >= %(min_price)s::numeric
"""

RANK_OPTIONS = {
    "Days up": "up_days",
    "Days down": "down_days",
    "Days up + days down": "moves",
}


def summarize(raw: pd.DataFrame) -> pd.DataFrame:
    """Fix types and add derived columns. Does not filter or sort."""
    df = raw.copy()
    for col in ("up_days", "down_days", "ignored_days", "days"):
        df[col] = df[col].astype(int)
    for col in ("avg_value", "avg_close"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    df["moves"] = df["up_days"] + df["down_days"]
    df["up_share"] = (df["up_days"] / df["moves"] * 100).where(df["moves"] > 0)
    return df


def rank(raw: pd.DataFrame, rank_by: str, top: int = TOP_N) -> pd.DataFrame:
    """rank_by is a key of RANK_OPTIONS. Ties are listed alphabetically by code."""
    key = RANK_OPTIONS[rank_by]
    df = summarize(raw)
    df = df[df[key] > 0]
    df = df.sort_values([key, "code"], ascending=[False, True])
    return df.head(top).reset_index(drop=True)


def period_change(rows: pd.DataFrame) -> pd.Series:
    """Percent change from each stock's first close in the period to its last close.

    `rows` has columns code, date, close, change_pct (exchange-reported daily change, in percent).
    Daily returns after the first day are compounded. Each day uses the close-to-close return,
    except on days where it disagrees with the reported change by ACTION_GAP_PTS or more. Those
    are ex-dividend, bonus-share or split days: the raw close drops, but holders lose nothing,
    and the reported change reflects that. A stock with a single day in the period has no change
    and is absent from the result.
    """
    df = rows.sort_values(["code", "date"]).copy()
    df["close"] = df["close"].astype(float)
    df["change_pct"] = df["change_pct"].astype(float)
    raw = df.groupby("code")["close"].pct_change() * 100
    use_reported = raw.isna() | ((df["change_pct"] - raw).abs() >= ACTION_GAP_PTS)
    daily = df["change_pct"].where(use_reported, raw)
    keep = df.groupby("code").cumcount() > 0          # the first day's move is before the period
    growth = 1 + daily[keep] / 100
    return (growth.groupby(df.loc[keep, "code"]).prod() - 1) * 100