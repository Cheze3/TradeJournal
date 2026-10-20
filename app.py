import hmac
import io

import pandas as pd
import psycopg
import streamlit as st

import db
from parsing import FileError, merge_frames, parse_cafef
from screening import RANK_OPTIONS, period_change, rank, summarize

st.set_page_config(page_title="HOSE Price Screener", layout="wide")


def password_gate() -> bool:
    if st.session_state.get("authed"):
        return True
    st.title("HOSE Price Screener")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), st.secrets["app_password"].encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Wrong password.")
    return False


@st.cache_data(show_spinner="Reading file...")
def read_file(name: str, data: bytes):
    return parse_cafef(io.BytesIO(data), name)


def show_status(url: str) -> None:
    try:
        s = db.status(url)
    except psycopg.Error as exc:
        st.error("Cannot reach the database. Wait a minute and reload the page.")
        with st.expander("Technical details"):
            st.code(str(exc))
        st.stop()
    if s["rows"] == 0:
        st.info("The database is empty. Upload your first CafeF file below.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Stocks", f"{s['stocks']:,}")
    c2.metric("Trading days covered", f"{s['date_min']:%d/%m/%Y} to {s['date_max']:%d/%m/%Y}")
    c3.metric("Last upload", f"{s['last_upload']:%d/%m/%Y %H:%M}" if s["last_upload"] else "-")


def upload_page(url: str) -> None:
    st.header("Update data")
    st.write("Upload the price history files downloaded from CafeF (.xlsx). "
             "You can select several files at once. Days already stored are refreshed "
             "with the values in the new file; nothing is deleted.")

    key = st.session_state.setdefault("uploader_key", 0)
    files = st.file_uploader("CafeF files", type=["xlsx"], accept_multiple_files=True,
                             key=f"uploader_{key}")
    if not files:
        return

    frames, names, failed = [], [], False
    for f in files:
        try:
            df, rep = read_file(f.name, f.getvalue())
        except FileError as exc:
            st.error(str(exc))
            failed = True
            continue
        frames.append(df)
        names.append(f.name)
        st.success(f"{f.name}: {rep.rows:,} rows, {rep.stocks} stocks, "
                   f"{rep.date_min:%d/%m/%Y} to {rep.date_max:%d/%m/%Y}")
        for w in rep.warnings:
            st.warning(f"{f.name}: {w}")

    if failed:
        st.info("Remove or replace the files marked in red, then try again. Nothing was saved.")
        return

    merged = merge_frames(frames)
    try:
        p = db.preview(url, merged)
    except psycopg.Error as exc:
        st.error("Cannot reach the database. Wait a minute and try again.")
        with st.expander("Technical details"):
            st.code(str(exc))
        return

    st.subheader("Ready to save")
    st.write(f"**{p['new']:,}** new rows will be added. "
             f"**{p['existing']:,}** rows already in the database will be refreshed.")

    if st.button("Save to database", type="primary"):
        try:
            with st.spinner("Saving..."):
                result = db.save(url, merged, names)
        except psycopg.Error as exc:
            st.error("The save failed and nothing was changed. Try again in a minute.")
            with st.expander("Technical details"):
                st.code(str(exc))
            return
        st.cache_data.clear()
        st.session_state["uploader_key"] += 1
        st.session_state["last_save"] = result
        st.rerun()


PRESETS = {"Last 5 trading days": 5, "Last 20 trading days": 20,
           "Last 60 trading days": 60, "All stored data": None}


@st.cache_data(show_spinner=False)
def cached_dates(_url: str):
    return db.trading_dates(_url)


@st.cache_data(show_spinner=False)
def cached_codes(_url: str):
    return db.trading_codes(_url)


@st.cache_data(show_spinner="Searching...")
def cached_screen(_url: str, start, end, threshold, min_value, min_price, code=None):
    return db.run_screen(_url, start, end, threshold, min_value, min_price, code)


@st.cache_data(show_spinner=False)
def cached_changes(_url: str, codes: tuple, start, end):
    return db.period_changes(_url, list(codes), start, end)


@st.cache_data(show_spinner=False)
def cached_history(_url: str, code: str, start, end):
    return db.stock_history(_url, code, start, end)


def summary_table(stats: pd.DataFrame, changes: pd.DataFrame, threshold: int) -> pd.DataFrame:
    change = period_change(changes).reindex(stats["code"]).to_numpy()
    return pd.DataFrame({
        "Stock": stats["code"],
        "Price change (%)": pd.Series(change, index=stats.index).round(1),
        f"Days up {threshold}%+": stats["up_days"],
        f"Days down {threshold}%+": stats["down_days"],
        "Up share (%)": stats["up_share"].round(0).astype("Int64"),
        "Days in period": stats["days"],
        "Avg daily value (bn VND)": stats["avg_value"].round(1),
        "Avg price (thousand VND)": stats["avg_close"].round(2),
    })


def excel_bytes(table: pd.DataFrame, sheet: str) -> bytes:
    buffer = io.BytesIO()
    table.to_excel(buffer, index=False, sheet_name=sheet)
    return buffer.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PERIOD_CHANGE_NOTE = (
    "Price change is measured from the stock's first close in the period to its last close. "
    "Days when a stock goes ex-dividend or splits are corrected, so those drops do not count as losses."
)


def ignored_note(ignored: int) -> None:
    if ignored:
        st.caption(f"{ignored} daily moves beyond the 7% limit were ignored "
                   "(for example the first trading day of a new listing).")


def stock_view(url: str, code: str, start, end, threshold: int, min_value: float) -> None:
    raw = cached_screen(url, start, end, threshold, 0.0, 0.0, code)
    history = cached_history(url, code, start, end)
    if raw.empty or history.empty:
        st.info(f"{code} has no data between {start:%d/%m/%Y} and {end:%d/%m/%Y}.")
        return

    stats = summarize(raw)
    table = summary_table(stats, cached_changes(url, (code,), start, end), threshold)
    st.subheader(f"{code}, {start:%d/%m/%Y} to {end:%d/%m/%Y}")
    st.dataframe(table, hide_index=True)
    st.caption(PERIOD_CHANGE_NOTE)
    ignored_note(int(stats.loc[0, "ignored_days"]))
    avg_value = float(stats.loc[0, "avg_value"])
    if avg_value < min_value:
        st.warning(f"{code} trades about {avg_value:.1f} billion VND a day, below the {min_value:g} billion "
                   "minimum used in the ranking. Filters do not apply to a searched stock. With so little "
                   "trading, price moves can come from a few small trades.")

    daily = pd.DataFrame({
        "Date": history["date"].map(lambda d: f"{d:%d/%m/%Y}"),
        "Close (thousand VND)": history["close"],
        "Change (%)": history["change_pct"],
        "Volume (shares)": history["volume"],
        "Traded value (bn VND)": history["value"],
    }).iloc[::-1]
    st.subheader("Daily data")
    st.dataframe(daily, hide_index=True)
    st.download_button("Download daily data as Excel", excel_bytes(daily, code),
                       file_name=f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.xlsx", mime=XLSX)


def screener_page(url: str) -> None:
    st.header("Price-change screener")
    try:
        dates = cached_dates(url)
        codes = cached_codes(url)
    except psycopg.Error as exc:
        st.error("Cannot reach the database. Wait a minute and reload the page.")
        with st.expander("Technical details"):
            st.code(str(exc))
        return
    if not dates:
        st.info("The database is empty. Use the Update data page first.")
        return

    code = st.selectbox("Search a stock", codes, index=None,
                        placeholder="Type a stock code, for example VNM",
                        help="Shows one stock on its own. The minimum trading value and price filters are not applied.")

    left, right = st.columns(2)
    with left:
        period = st.selectbox("Period", [*PRESETS, "Choose dates"], index=1)
        if period == "Choose dates":
            start = st.date_input("From", value=dates[max(0, len(dates) - 20)],
                                  min_value=dates[0], max_value=dates[-1])
            end = st.date_input("To", value=dates[-1], min_value=dates[0], max_value=dates[-1])
        else:
            n = PRESETS[period]
            start = dates[0] if n is None else dates[-min(n, len(dates))]
            end = dates[-1]
    with right:
        threshold = st.select_slider("Price change of at least", options=list(range(1, 8)),
                                     value=3, format_func=lambda x: f"{x}%")
        rank_by = None if code else st.radio("Rank stocks by", list(RANK_OPTIONS), horizontal=True)

    min_value, min_price = 5.0, 0.0
    if not code:
        with st.expander("Filters"):
            min_value = st.number_input("Minimum average daily traded value (billion VND)",
                                        min_value=0.0, value=5.0, step=1.0)
            min_price = st.number_input("Minimum average price (thousand VND)",
                                        min_value=0.0, value=0.0, step=1.0)

    if start > end:
        st.error("The start date must be before the end date.")
        return

    if code:
        st.caption("Showing the searched stock only. Clear the search box to go back to the ranking.")
        stock_view(url, code, start, end, threshold, min_value)
        return

    raw = cached_screen(url, start, end, threshold, min_value, min_price)
    top = rank(raw, rank_by)
    if top.empty:
        st.info("No stock matches these settings. Try a lower percentage or relax the filters.")
        return

    st.write(
        f"Between **{start:%d/%m/%Y}** and **{end:%d/%m/%Y}**, a day counts as *up* when a stock's "
        f"close rose by {threshold}% or more, and as *down* when it fell by {threshold}% or more. "
        f"Showing the top {len(top)} of {len(raw)} stocks that pass the filters, ranked by "
        f"{rank_by.lower()}. Up share = days up / (days up + days down)."
    )
    ignored_note(int(raw["ignored_days"].astype(int).sum()))

    table = summary_table(top, cached_changes(url, tuple(top["code"]), start, end), threshold)
    st.dataframe(table, hide_index=True)
    st.caption(PERIOD_CHANGE_NOTE)
    st.download_button("Download as Excel", excel_bytes(table, "Screener"),
                       file_name=f"screener_{start:%Y%m%d}_{end:%Y%m%d}_{threshold}pct.xlsx", mime=XLSX)
    st.caption("This table describes past price moves. It does not predict future prices.")


def delete_page(url: str) -> None:
    st.header("Delete data")
    st.warning("Deleting cannot be undone. You can restore data by uploading the original "
               "CafeF files again.")
    dates = cached_dates(url)
    if not dates:
        st.info("The database is already empty.")
        return

    key = st.session_state.setdefault("delete_key", 0)
    mode = st.radio("What do you want to delete?", ["A range of dates", "Everything"])

    if mode == "A range of dates":
        left, right = st.columns(2)
        start = left.date_input("From", value=dates[0], min_value=dates[0], max_value=dates[-1])
        end = right.date_input("To", value=dates[0], min_value=dates[0], max_value=dates[-1])
        if start > end:
            st.error("The start date must be before the end date.")
            return
        count = db.count_rows(url, start, end)
        if count == 0:
            st.info("No stored rows fall in this range.")
            return
        st.write(f"This will delete **{count:,}** rows from {start:%d/%m/%Y} to {end:%d/%m/%Y}.")
        phrase = "DELETE"
    else:
        count = db.count_rows(url)
        st.write(f"This will delete **all {count:,}** rows and the upload history.")
        phrase = "DELETE ALL"

    typed = st.text_input(f"Type {phrase} to confirm", key=f"delete_confirm_{key}")
    if st.button("Delete", type="primary", disabled=typed.strip() != phrase):
        try:
            with st.spinner("Deleting..."):
                if mode == "A range of dates":
                    removed = db.delete_range(url, start, end)
                else:
                    removed = db.delete_all(url)
        except psycopg.Error as exc:
            st.error("The delete failed and nothing was changed. Try again in a minute.")
            with st.expander("Technical details"):
                st.code(str(exc))
            return
        st.cache_data.clear()
        st.session_state["delete_key"] += 1
        st.session_state["last_delete"] = removed
        st.rerun()


def main() -> None:
    if not password_gate():
        return
    url = st.secrets["database_url"]

    st.title("HOSE Price Screener")
    if "last_save" in st.session_state:
        r = st.session_state.pop("last_save")
        st.success(f"Saved. {r['new']:,} rows added, {r['updated']:,} rows refreshed.")
    if "last_delete" in st.session_state:
        st.success(f"Deleted {st.session_state.pop('last_delete'):,} rows.")
    show_status(url)

    page = st.sidebar.radio("Page", ["Screener", "Update data", "Delete data"])
    if page == "Screener":
        screener_page(url)
    elif page == "Update data":
        upload_page(url)
    else:
        delete_page(url)


main()