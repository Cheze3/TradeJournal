# HOSE Price Screener (upload page)

## Setup
1. Create a Supabase project. In the SQL editor, run `schema.sql`.
2. In Supabase, click Connect and copy the **Session pooler** connection string
   (the direct connection may require IPv6; the transaction pooler is not needed).
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in
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
```

## Behavior
- A file is checked before anything is written. Any problem rejects the whole upload.
- Saving is one transaction: it either fully succeeds or changes nothing.
- Rows are matched on (code, date). Existing rows are overwritten, new rows are added, nothing is deleted.
- `change_pct` is the exchange-reported daily change in percent, parsed from the "Thay đổi" column.
