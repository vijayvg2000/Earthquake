import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

st.set_page_config(
    page_title="Earthquake Analytics",
    page_icon="🌎",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🌎 Earthquake Analytics Dashboard")
st.caption("USGS earthquake data • Python • MySQL • Streamlit")

@st.cache_resource
def get_engine():
    required = ["MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
            + ". Copy .env.example to .env and configure your MySQL credentials."
        )
    url = URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE"),
    )
    return create_engine(url, pool_pre_ping=True)

# Each analysis returns a MySQL 8-compatible SELECT query.
# Queries involving continent require a populated `continent` column.
QUERIES = {
"1. Top 10 strongest earthquakes": """
SELECT id, time, place, country, mag, depth_km, latitude, longitude, status
FROM earthquakes WHERE mag IS NOT NULL
ORDER BY mag DESC, time DESC LIMIT 10
""",
"2. Top 10 deepest earthquakes": """
SELECT id, time, place, country, mag, depth_km, latitude, longitude
FROM earthquakes WHERE depth_km IS NOT NULL
ORDER BY depth_km DESC LIMIT 10
""",
"3. Shallow earthquakes below 50 km and magnitude above 7.5": """
SELECT id, time, place, country, mag, depth_km, latitude, longitude
FROM earthquakes
WHERE depth_km < 50 AND mag > 7.5
ORDER BY mag DESC
""",
"4. Average depth per continent": """
SELECT continent, AVG(depth_km) AS average_depth_km, COUNT(*) AS event_count
FROM earthquakes
WHERE continent IS NOT NULL AND depth_km IS NOT NULL
GROUP BY continent ORDER BY average_depth_km DESC
""",
"5. Average magnitude per magnitude type": """
SELECT magType, AVG(mag) AS average_magnitude, COUNT(*) AS event_count
FROM earthquakes
WHERE magType IS NOT NULL AND mag IS NOT NULL
GROUP BY magType ORDER BY average_magnitude DESC
""",
"6. Year with most earthquakes": """
SELECT year, COUNT(*) AS earthquake_count
FROM earthquakes WHERE year IS NOT NULL
GROUP BY year ORDER BY earthquake_count DESC LIMIT 1
""",
"7. Month with highest number of earthquakes": """
SELECT month, COUNT(*) AS earthquake_count
FROM earthquakes WHERE month IS NOT NULL
GROUP BY month ORDER BY earthquake_count DESC LIMIT 1
""",
"8. Day of week with most earthquakes": """
SELECT day_of_week,
       CASE day_of_week WHEN 0 THEN 'Monday' WHEN 1 THEN 'Tuesday'
       WHEN 2 THEN 'Wednesday' WHEN 3 THEN 'Thursday'
       WHEN 4 THEN 'Friday' WHEN 5 THEN 'Saturday' WHEN 6 THEN 'Sunday'
       ELSE 'Unknown' END AS weekday_name,
       COUNT(*) AS earthquake_count
FROM earthquakes WHERE day_of_week IS NOT NULL
GROUP BY day_of_week ORDER BY earthquake_count DESC LIMIT 1
""",
"9. Earthquake count per hour of day": """
SELECT HOUR(time) AS hour_utc, COUNT(*) AS earthquake_count
FROM earthquakes WHERE time IS NOT NULL
GROUP BY HOUR(time) ORDER BY hour_utc
""",
"10. Most active reporting network": """
SELECT net, COUNT(*) AS event_count
FROM earthquakes WHERE net IS NOT NULL
GROUP BY net ORDER BY event_count DESC LIMIT 10
""",
"11. Top 5 places with highest casualties (requires impact data)": None,
"12. Estimated economic loss per continent (requires impact data)": None,
"13. Average economic loss by alert level (requires impact data)": None,
"14. Reviewed vs automatic events": """
SELECT status, COUNT(*) AS event_count
FROM earthquakes WHERE status IS NOT NULL
GROUP BY status ORDER BY event_count DESC
""",
"15. Count by event type": """
SELECT type, COUNT(*) AS event_count
FROM earthquakes WHERE type IS NOT NULL
GROUP BY type ORDER BY event_count DESC
""",
"16. Number of earthquakes by data type": """
SELECT TRIM(type_item) AS data_type, COUNT(*) AS event_count
FROM earthquakes
JOIN JSON_TABLE(
  CONCAT('["', REPLACE(REPLACE(COALESCE(types, ''), ',', '","'), '"', '\\\\"'), '"]'),
  '$[*]' COLUMNS(type_item VARCHAR(100) PATH '$')
) AS jt
WHERE TRIM(type_item) <> ''
GROUP BY TRIM(type_item) ORDER BY event_count DESC
""",
"17. Average RMS and gap per continent": """
SELECT continent, AVG(rms) AS average_rms, AVG(gap) AS average_gap,
       COUNT(*) AS event_count
FROM earthquakes WHERE continent IS NOT NULL
GROUP BY continent ORDER BY average_rms DESC
""",
"18. Events with high station coverage (nst > threshold)": """
SELECT id, time, place, country, mag, nst, status
FROM earthquakes WHERE nst > :threshold
ORDER BY nst DESC LIMIT 500
""",
"19. Tsunami-triggering events per year": """
SELECT year, COUNT(*) AS tsunami_event_count
FROM earthquakes WHERE tsunami = 1 AND year IS NOT NULL
GROUP BY year ORDER BY year
""",
"20. Count earthquakes by alert level": """
SELECT LOWER(alert) AS alert_level, COUNT(*) AS event_count
FROM earthquakes WHERE alert IS NOT NULL
GROUP BY LOWER(alert) ORDER BY event_count DESC
""",
"21. Top 5 countries by average magnitude in the past 5 years": """
SELECT country, AVG(mag) AS average_magnitude, COUNT(*) AS event_count
FROM earthquakes
WHERE country IS NOT NULL AND mag IS NOT NULL
  AND time >= :cutoff
GROUP BY country HAVING COUNT(*) >= 5
ORDER BY average_magnitude DESC LIMIT 5
""",
"22. Countries with shallow and deep events in the same month": """
SELECT country, year, month,
       SUM(depth_km < 70) AS shallow_events,
       SUM(depth_km > 300) AS deep_events
FROM earthquakes
WHERE country IS NOT NULL AND depth_km IS NOT NULL
GROUP BY country, year, month
HAVING SUM(depth_km < 70) > 0 AND SUM(depth_km > 300) > 0
ORDER BY year DESC, month DESC, country
""",
"23. Year-over-year global earthquake growth rate": """
WITH yearly AS (
  SELECT year, COUNT(*) AS event_count
  FROM earthquakes WHERE year IS NOT NULL
  GROUP BY year
)
SELECT year, event_count,
       LAG(event_count) OVER (ORDER BY year) AS previous_year_count,
       ROUND(100.0 * (event_count - LAG(event_count) OVER (ORDER BY year))
       / NULLIF(LAG(event_count) OVER (ORDER BY year), 0), 2) AS yoy_growth_percent
FROM yearly ORDER BY year
""",
"24. Top 3 active regions combining frequency and average magnitude": """
SELECT COALESCE(country, place) AS region,
       COUNT(*) AS event_count, AVG(mag) AS average_magnitude,
       ROUND(COUNT(*) * AVG(mag), 2) AS activity_score
FROM earthquakes
WHERE mag IS NOT NULL AND COALESCE(country, place) IS NOT NULL
GROUP BY COALESCE(country, place)
HAVING COUNT(*) >= 5
ORDER BY activity_score DESC LIMIT 3
""",
"25. Average depth by country near the equator (±5° latitude)": """
SELECT country, AVG(depth_km) AS average_depth_km, COUNT(*) AS event_count
FROM earthquakes
WHERE country IS NOT NULL AND latitude BETWEEN -5 AND 5
  AND depth_km IS NOT NULL
GROUP BY country ORDER BY average_depth_km DESC
""",
"26. Countries with highest shallow-to-deep earthquake ratio": """
SELECT country,
       SUM(depth_km < 70) AS shallow_count,
       SUM(depth_km > 300) AS deep_count,
       ROUND(SUM(depth_km < 70) / NULLIF(SUM(depth_km > 300), 0), 3) AS shallow_deep_ratio
FROM earthquakes
WHERE country IS NOT NULL AND depth_km IS NOT NULL
GROUP BY country
HAVING SUM(depth_km > 300) > 0
ORDER BY shallow_deep_ratio DESC LIMIT 20
""",
"27. Average magnitude difference: tsunami vs no tsunami": """
SELECT
  AVG(CASE WHEN tsunami = 1 THEN mag END) AS average_tsunami_magnitude,
  AVG(CASE WHEN tsunami = 0 THEN mag END) AS average_no_tsunami_magnitude,
  AVG(CASE WHEN tsunami = 1 THEN mag END)
    - AVG(CASE WHEN tsunami = 0 THEN mag END) AS magnitude_difference
FROM earthquakes WHERE mag IS NOT NULL AND tsunami IN (0, 1)
""",
"28. Lowest reliability using RMS and gap": """
SELECT id, time, place, country, mag, rms, gap, nst,
       (COALESCE(rms, 0) + COALESCE(gap, 0) / 180.0) AS simple_quality_risk_score
FROM earthquakes
WHERE rms IS NOT NULL OR gap IS NOT NULL
ORDER BY simple_quality_risk_score DESC LIMIT 100
""",
"29. Consecutive earthquakes within 50 km and 1 hour": """
WITH ordered_events AS (
  SELECT id, time, place, latitude, longitude, mag,
         LEAD(id) OVER (ORDER BY time, id) AS next_id,
         LEAD(time) OVER (ORDER BY time, id) AS next_time,
         LEAD(place) OVER (ORDER BY time, id) AS next_place,
         LEAD(latitude) OVER (ORDER BY time, id) AS next_latitude,
         LEAD(longitude) OVER (ORDER BY time, id) AS next_longitude,
         LEAD(mag) OVER (ORDER BY time, id) AS next_mag
  FROM earthquakes
  WHERE time IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL
)
SELECT id AS event_1_id, time AS event_1_time, place AS event_1_place,
       next_id AS event_2_id, next_time AS event_2_time, next_place AS event_2_place,
       ROUND(6371 * 2 * ASIN(SQRT(
         POWER(SIN(RADIANS(next_latitude - latitude) / 2), 2) +
         COS(RADIANS(latitude)) * COS(RADIANS(next_latitude)) *
         POWER(SIN(RADIANS(next_longitude - longitude) / 2), 2)
       )), 2) AS distance_km,
       TIMESTAMPDIFF(SECOND, time, next_time) AS seconds_apart
FROM ordered_events
WHERE next_time > time
  AND TIMESTAMPDIFF(SECOND, time, next_time) <= 3600
  AND 6371 * 2 * ASIN(SQRT(
       POWER(SIN(RADIANS(next_latitude - latitude) / 2), 2) +
       COS(RADIANS(latitude)) * COS(RADIANS(next_latitude)) *
       POWER(SIN(RADIANS(next_longitude - longitude) / 2), 2)
  )) <= 50
ORDER BY seconds_apart, distance_km LIMIT 500
""",
"30. Regions with most deep-focus earthquakes (>300 km)": """
SELECT COALESCE(country, place) AS region,
       COUNT(*) AS deep_event_count, AVG(mag) AS average_magnitude
FROM earthquakes
WHERE depth_km > 300 AND COALESCE(country, place) IS NOT NULL
GROUP BY COALESCE(country, place)
ORDER BY deep_event_count DESC LIMIT 20
""",
}

st.sidebar.header("Dashboard controls")
analysis_name = st.sidebar.selectbox("Choose analysis", list(QUERIES.keys()))
nst_threshold = st.sidebar.number_input("Station coverage threshold (nst)", min_value=0, value=20, step=5)
cutoff_date = st.sidebar.date_input("5-year analysis cutoff", value=date.today() - timedelta(days=365*5))
st.sidebar.caption("Times are interpreted in UTC. Some analyses require enriched data.")

try:
    engine = get_engine()
    with engine.connect() as conn:
        # Check connection and display headline metrics.
        total = conn.execute(text("SELECT COUNT(*) FROM earthquakes")).scalar_one()
        max_mag = conn.execute(text("SELECT MAX(mag) FROM earthquakes")).scalar_one()
        avg_mag = conn.execute(text("SELECT AVG(mag) FROM earthquakes")).scalar_one()
        tsunami_count = conn.execute(text("SELECT COUNT(*) FROM earthquakes WHERE tsunami = 1")).scalar_one()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total events", f"{total:,}")
    c2.metric("Maximum magnitude", "N/A" if max_mag is None else f"{max_mag:.2f}")
    c3.metric("Average magnitude", "N/A" if avg_mag is None else f"{avg_mag:.2f}")
    c4.metric("Tsunami-flagged events", f"{tsunami_count:,}")

    st.divider()
    st.subheader(analysis_name)

    query = QUERIES[analysis_name]
    if query is None:
        st.warning(
            "This analysis cannot be calculated from the standard 26 USGS features alone. "
            "Add an external impact table with casualty counts and/or economic loss, then join it to earthquakes."
        )
    else:
        params = {}
        if ":threshold" in query:
            params["threshold"] = int(nst_threshold)
        if ":cutoff" in query:
            params["cutoff"] = pd.Timestamp(cutoff_date, tz="UTC").to_pydatetime()

        with engine.connect() as conn:
            result_df = pd.read_sql_query(text(query), conn, params=params)

        st.caption(f"{len(result_df):,} result row(s)")
        st.dataframe(result_df, use_container_width=True, hide_index=True)

        if not result_df.empty:
            numeric_cols = result_df.select_dtypes(include="number").columns.tolist()
            chart_col = next(
                (c for c in ["event_count", "earthquake_count", "deep_event_count",
                             "tsunami_event_count", "average_magnitude", "average_depth_km",
                             "shallow_deep_ratio", "yoy_growth_percent", "activity_score"]
                 if c in result_df.columns),
                None
            )
            category_col = next(
                (c for c in ["year", "month", "weekday_name", "net", "status", "type",
                             "alert_level", "magType", "country", "continent", "region",
                             "data_type", "hour_utc", "depth_category"]
                 if c in result_df.columns),
                None
            )
            if chart_col and category_col and chart_col in numeric_cols:
                st.subheader("Chart")
                chart_data = result_df[[category_col, chart_col]].dropna().copy()
                if not chart_data.empty:
                    chart_data[category_col] = chart_data[category_col].astype(str)
                    st.bar_chart(chart_data.set_index(category_col)[chart_col])

        st.download_button(
            "Download results as CSV",
            data=result_df.to_csv(index=False).encode("utf-8"),
            file_name="earthquake_analysis.csv",
            mime="text/csv",
        )

    with st.expander("Database and data requirements"):
        st.markdown("""
- The table must be named `earthquakes`.
- The app expects the original fields and derived columns used by the Python pipeline.
- Queries 4 and 17 need a populated `continnte` column.
- Query 11 needs a casualty/impact dataset; query 12 needs economic-loss estimates joined to a continent; query 13 needs economic loss and alert levels.
- Query 16 uses MySQL 8 `JSON_TABLE` to split the comma-separated `types` field.
- Query 23 uses MySQL 8 window functions.
- Query 29 uses MySQL 8 window functions and the Haversine formula; it compares each event with the next event in global time order.
- The country parsed from `place` is only a heuristic. Enrich geography before using country/continent results for formal reporting.
- The RMS/gap score in query 28 is a simple ranking heuristic, not a scientifically validated reliability metric.
        """)

except (RuntimeError, SQLAlchemyError) as exc:
    st.error("Could not connect to MySQL or execute the dashboard.")
    st.code(str(exc))
    st.info("Check that MySQL is running, the database exists, the `earthquakes` table is populated, and `.env` credentials are correct.")
finally:
    try:
        engine.dispose()
    except Exception:
        pass
