"""Run with: python tests/test_parsing.py   (or pytest)"""
import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsing import FileError, merge_frames, parse_cafef  # noqa: E402

HEADERS = ["Mã", "Ngày", "Giá đóng cửa", "Giá điều chỉnh", "Thay đổi", "Khối lượng khớp lệnh",
           "Giá trị khớp lệnh", "Khối lượng thỏa thuận", "Giá trị thỏa thuận",
           "Giá mở cửa", "Giá cao nhất", "Giá thấp nhất"]


def make(rows, headers=HEADERS) -> io.BytesIO:
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=headers).to_excel(buf, index=False)
    buf.seek(0)
    return buf


def row(code="AAA", date="15/10/2026", close=7.0, change="-0,06 (-0,85%)"):
    return [code, date, close, close, change, 1000, 1.5, 0, 0, 7.1, 7.2, 6.9]


def expect_error(rows, text, headers=HEADERS):
    try:
        parse_cafef(make(rows, headers), "t.xlsx")
    except FileError as exc:
        assert text in str(exc), str(exc)
        return
    raise AssertionError(f"no FileError, expected: {text}")


def test_valid_and_change_formats():
    rows = [row(change="-0,06 (-0,85%)"), row("BBB", change="0 (0,00%)"),
            row("CCC", change="+0,12 (+6,82%)")]
    df, rep = parse_cafef(make(rows), "t.xlsx")
    assert list(df["change_pct"]) == [-0.85, 0.0, 6.82]
    assert rep.rows == 3 and rep.stocks == 3
    assert str(df["date"].iloc[0]) == "2026-10-15"


def test_code_is_normalised():
    df, _ = parse_cafef(make([row(code=" aaa ")]), "t.xlsx")
    assert df["code"].iloc[0] == "AAA"


def test_missing_column():
    expect_error([row()[:2]], "Missing columns", headers=HEADERS[:2])


def test_bad_date():
    expect_error([row(date="2026-10-15")], "dd/mm/yyyy")


def test_bad_change_text():
    expect_error([row(change="n/a")], "daily change")


def test_bad_close():
    expect_error([row(close=0)], "closing price")


def test_duplicates_dropped_with_warning():
    df, rep = parse_cafef(make([row(close=7.0), row(close=7.5)]), "t.xlsx")
    assert len(df) == 1 and df["close"].iloc[0] == 7.5
    assert any("duplicate" in w for w in rep.warnings)


def test_merge_later_file_wins():
    a, _ = parse_cafef(make([row(close=7.0)]), "a.xlsx")
    b, _ = parse_cafef(make([row(close=8.0)]), "b.xlsx")
    m = merge_frames([a, b])
    assert len(m) == 1 and m["close"].iloc[0] == 8.0


def test_not_an_excel_file():
    try:
        parse_cafef(io.BytesIO(b"hello"), "x.xlsx")
    except FileError as exc:
        assert "could not be opened" in str(exc)
        return
    raise AssertionError("no FileError")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    for p in sys.argv[1:]:  # optional: real files
        df, rep = parse_cafef(p, Path(p).name)
        print(p, rep)
