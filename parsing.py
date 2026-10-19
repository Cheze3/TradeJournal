"""Read and validate CafeF price-history Excel files.

Every error raised as FileError carries a message that is safe to show to the user.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# CafeF header -> internal name. Negotiated-trade columns are not needed.
REQUIRED = {
    "Mã": "code",
    "Ngày": "date",
    "Giá đóng cửa": "close",
    "Giá điều chỉnh": "adj_close",
    "Thay đổi": "change_text",
    "Khối lượng khớp lệnh": "volume",
    "Giá trị khớp lệnh": "value",
    "Giá mở cửa": "open",
    "Giá cao nhất": "high",
    "Giá thấp nhất": "low",
}
DB_COLUMNS = ["code", "date", "close", "adj_close", "change_pct",
              "volume", "value", "open", "high", "low"]
NUMERIC_OPTIONAL = ["adj_close", "volume", "value", "open", "high", "low"]
CHANGE_PATTERN = r"\(\s*([+-]?\d+(?:[.,]\d+)?)\s*%\s*\)"
LARGE_MOVE_PCT = 20.0  # first-day-of-listing moves reach 20% on HOSE


class FileError(ValueError):
    """The file cannot be used. The message is written for the end user."""


@dataclass
class Report:
    filename: str
    rows: int
    stocks: int
    date_min: object
    date_max: object
    warnings: list[str] = field(default_factory=list)


def _excel_rows(index) -> str:
    """Excel row numbers (header is row 1) for the first few offending rows."""
    shown = [str(i + 2) for i in list(index)[:3]]
    return ", ".join(shown)


def parse_cafef(source, filename: str = "file") -> tuple[pd.DataFrame, Report]:
    try:
        raw = pd.read_excel(source, dtype={"Mã": str})
    except Exception as exc:  # corrupt file, wrong format, password protected, ...
        raise FileError(f"{filename}: could not be opened as an Excel (.xlsx) file.") from exc

    raw.columns = [str(c).strip() for c in raw.columns]
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        raise FileError(
            f"{filename}: this does not look like a CafeF price history file. "
            f"Missing columns: {', '.join(missing)}."
        )
    if raw.empty:
        raise FileError(f"{filename}: the file has no data rows.")

    df = raw[list(REQUIRED)].rename(columns=REQUIRED)
    warnings: list[str] = []

    # code
    df["code"] = df["code"].astype("string").str.strip().str.upper()
    bad = df["code"].isna() | (df["code"] == "")
    if bad.any():
        raise FileError(f"{filename}: {int(bad.sum())} rows have no stock code "
                        f"(Excel rows {_excel_rows(df.index[bad])}).")

    # date (dd/mm/yyyy text, or real Excel dates)
    if pd.api.types.is_datetime64_any_dtype(df["date"]):
        parsed = df["date"]
    else:
        parsed = pd.to_datetime(df["date"].astype("string").str.strip(),
                                format="%d/%m/%Y", errors="coerce")
    bad = parsed.isna()
    if bad.any():
        raise FileError(f"{filename}: {int(bad.sum())} rows have a date that is not in "
                        f"dd/mm/yyyy format (Excel rows {_excel_rows(df.index[bad])}).")
    df["date"] = parsed.dt.date

    # close (required)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    bad = df["close"].isna() | (df["close"] <= 0)
    if bad.any():
        raise FileError(f"{filename}: {int(bad.sum())} rows have a missing or invalid closing "
                        f"price (Excel rows {_excel_rows(df.index[bad])}).")

    # daily change % (required), parsed from text such as "-0,06 (-0,85%)"
    text = df["change_text"].astype("string")
    pct = text.str.extract(CHANGE_PATTERN)[0].str.replace(",", ".", regex=False)
    df["change_pct"] = pd.to_numeric(pct, errors="coerce")
    bad = df["change_pct"].isna()
    if bad.any():
        example = text[bad].iloc[0]
        raise FileError(f"{filename}: could not read the daily change in {int(bad.sum())} rows "
                        f"(Excel rows {_excel_rows(df.index[bad])}, e.g. '{example}').")

    # optional numbers: stored as empty when missing
    for col in NUMERIC_OPTIONAL:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        n_missing = int(df[col].isna().sum())
        if n_missing:
            warnings.append(f"{n_missing} rows have no value in '{col}'.")

    dup = df.duplicated(["code", "date"], keep="last")
    if dup.any():
        warnings.append(f"{int(dup.sum())} duplicate rows (same stock and date) were ignored.")
        df = df[~dup]

    large = int((df["change_pct"].abs() > LARGE_MOVE_PCT).sum())
    if large:
        warnings.append(f"{large} rows show a daily move above {LARGE_MOVE_PCT:.0f}%.")

    df = df[DB_COLUMNS].reset_index(drop=True)
    report = Report(filename=filename, rows=len(df), stocks=df["code"].nunique(),
                    date_min=df["date"].min(), date_max=df["date"].max(), warnings=warnings)
    return df, report


def merge_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine several files. When a stock/date appears twice, the later file wins."""
    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates(["code", "date"], keep="last").reset_index(drop=True)
