import hmac
import io

import psycopg
import streamlit as st

import db
from parsing import FileError, merge_frames, parse_cafef

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
        st.session_state["uploader_key"] += 1
        st.session_state["last_save"] = result
        st.rerun()


def main() -> None:
    if not password_gate():
        return
    url = st.secrets["database_url"]

    st.title("HOSE Price Screener")
    if "last_save" in st.session_state:
        r = st.session_state.pop("last_save")
        st.success(f"Saved. {r['new']:,} rows added, {r['updated']:,} rows refreshed.")
    show_status(url)

    page = st.sidebar.radio("Page", ["Update data", "Screener (coming next)"])
    if page == "Update data":
        upload_page(url)
    else:
        st.info("The screener page is built after the upload page is confirmed working.")


main()
