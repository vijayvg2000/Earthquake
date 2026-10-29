# Earthquake Analytics Dashboard

Python + MySQL + Streamlit dashboard for 30 earthquake analytics tasks.

## Setup

1. Ensure MySQL is running and your `earthquake_db` database contains the `earthquakes` table.
2. Create and activate a virtual environment (recommended).
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and edit the credentials:

   Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```

   macOS/Linux:
   ```bash
   cp .env.example .env
   ```

5. Run the dashboard:

   ```bash
   streamlit run app.py
   ```

## Important schema notes

- The app expects the 26 original USGS features and derived fields such as `year`, `month`, `day`, and `day_of_week`.
- `country` must be populated for country-level queries.
- `continent` must be added and populated for continent-level queries. Regex parsing of USGS `place` is not a reliable country/continent resolver.
- Casualties and economic loss are not included in the standard USGS earthquake feed. Tasks 11–13 need a separate impact dataset/table.
- Query 16 and queries using window functions require MySQL 8.0+.
- Query 29 compares each event with the next event globally by timestamp, then filters pairs within 1 hour and 50 km.
- Query 28's combined RMS/gap score is a heuristic, not a validated scientific metric.

## Security

Never commit `.env` to Git. Add it to `.gitignore`.
