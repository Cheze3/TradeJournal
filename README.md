# HOSE Price Screener

## Setup
1. Create a Supabase project. In the SQL editor, run `schema.sql`.
2. In Supabase, click Connect and copy the **Session pooler** connection string
   (the direct connection may require IPv6; the transaction pooler is not needed).
3. Create `.streamlit/secrets.toml` and fill in
   `app_password` and `database_url`.
4. Install and run:
   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   streamlit run app.py
   ```

## Tests (parser only, no database needed)
```
python tests/test_parsing.py                       # unit tests
python tests/test_parsing.py path/to/cafef.xlsx    # also parse a real file
python tests/test_screening.py path/to/cafef.xlsx  # screener counting logic (runs on SQLite)
```

## Behavior
- A file is checked before anything is written. Any problem rejects the whole upload.
- Saving is one transaction: it either fully succeeds or changes nothing.
- Rows are matched on (code, date). Existing rows are overwritten, new rows are added, nothing is deleted.
- `change_pct` is the exchange-reported daily change in percent, parsed from the "Thay đổi" column.

## Screener
- A day is "up" when the exchange-reported daily change is at least +X%, "down" when it is at most -X% (X = 1 to 7).
- Daily moves beyond 7.1% in either direction are ignored (e.g. first trading day of a new listing, where the limit is wider).
- Filters default to a minimum average daily traded value of 5 billion VND. Without it, the list fills with stocks that barely trade.
- Price change (%) is the change from a stock's first close in the period to its last close. Each day uses the close-to-close return, except on days where it differs from the exchange-reported change by 1 point or more (ex-dividend, bonus-share and split days), where the reported change is used so those drops do not count as losses.
- Search a stock shows one stock on its own, with its summary row and daily data. The minimum value and price filters are not applied, and a warning appears when the stock trades below the ranking's minimum value.
- Up/down day counts use the exchange-reported daily change, which is what traders see (a limit-up day shows +7.00%).
- Prices are in thousand VND and traded value in billion VND, as exported by CafeF.