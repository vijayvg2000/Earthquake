
import os
import re
import time
import logging
import json

import requests
import pandas as pd

from dotenv import load_dotenv
from sqlalchemy import create_engine, URL, text, inspect
from sqlalchemy.exc import SQLAlchemyError


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

START_YEAR = 2021
END_YEAR = 2025
MIN_MAGNITUDE = 4.0

TABLE_NAME = "earthquakes"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# =========================================================
# 1. FETCH DATA FROM USGS API
# =========================================================

def fetch_earthquake_data():
    records = []
    session = requests.Session()

    for year in range(START_YEAR, END_YEAR + 1):
        for month in range(1, 13):

            start_date = f"{year}-{month:02d}-01"

            if month == 12:
                end_date = f"{year + 1}-01-01"
            else:
                end_date = f"{year}-{month + 1:02d}-01"

            params = {
                "starttime": start_date,
                "endtime": end_date,
                "minmagnitude": MIN_MAGNITUDE,
                "format": "geojson",
                "orderby": "time-asc",
                "limit": 20000
            }

            try:
                response = session.get(
                    API_URL,
                    params=params,
                    timeout=60
                )
                response.raise_for_status()

                data = response.json()
                features = data.get("features", [])
                
                # print(json.dumps(features[:3], indent=4))

                # A capped response may be incomplete.
                if len(features) >= 20000:
                    logging.warning(
                        "%s returned 20,000 events. "
                        "Check whether this month needs smaller date ranges.",
                        start_date
                    )

                for feature in features:
                    properties = feature.get("properties", {})
                    geometry = feature.get("geometry") or {}
                    coordinates = geometry.get("coordinates") or []

                    records.append({
                        # 26 original features
                        "id": feature.get("id"),
                        "time": properties.get("time"),
                        "updated": properties.get("updated"),

                        "latitude": (
                            coordinates[1]
                            if len(coordinates) > 1 else None
                        ),
                        "longitude": (
                            coordinates[0]
                            if len(coordinates) > 0 else None
                        ),
                        "depth_km": (
                            coordinates[2]
                            if len(coordinates) > 2 else None
                        ),

                        "mag": properties.get("mag"),
                        "magType": properties.get("magType"),
                        "place": properties.get("place"),
                        "status": properties.get("status"),
                        "tsunami": properties.get("tsunami"),
                        "sig": properties.get("sig"),
                        "net": properties.get("net"),
                        "nst": properties.get("nst"),
                        "dmin": properties.get("dmin"),
                        "rms": properties.get("rms"),
                        "gap": properties.get("gap"),
                        "magError": properties.get("magError"),
                        "depthError": properties.get("depthError"),
                        "magNst": properties.get("magNst"),

                        "locationSource": properties.get("locationSource"),
                        "magSource": properties.get("magSource"),
                        "types": properties.get("types"),
                        "ids": properties.get("ids"),
                        "sources": properties.get("sources"),
                        "type": properties.get("type")
                    })

                logging.info(
                    "%s to %s: fetched %s events",
                    start_date,
                    end_date,
                    len(features)
                )

            except (requests.RequestException, ValueError) as error:
                logging.error(
                    "Failed to fetch %s to %s: %s",
                    start_date,
                    end_date,
                    error
                )

            time.sleep(0.5)

    session.close()

    if not records:
        raise RuntimeError(
            "No earthquake records were fetched. "
            "Check your internet connection and USGS API."
        )

    return pd.DataFrame(records)


# =========================================================
# 2. CLEAN AND TRANSFORM DATA
# =========================================================

def clean_earthquake_data(df):

    df = df.copy()

    # Convert Unix timestamps in milliseconds to UTC datetime.
    for column in ["time", "updated"]:
        df[column] = pd.to_datetime(
            df[column],
            unit="ms",
            utc=True,
            errors="coerce"
        )

    # Remove records without an event ID or occurrence time.
    df = df.dropna(subset=["id", "time"])

    df["id"] = df["id"].astype("string").str.strip()
    df = df[df["id"] != ""]

    # Keep the latest occurrence of each event ID in this batch.
    df = df.drop_duplicates(subset=["id"], keep="last")

    # Extract a possible country/location label from place.
    # This is a heuristic, not guaranteed country identification.
    def extract_country(place):
        if pd.isna(place):
            return None

        match = re.search(r",\s*([^,]+)\s*$", str(place))

        return match.group(1).strip() if match else None

    df["country"] = df["place"].apply(extract_country)
    
    # -----------------------------------------------------
    # Normalize alert field to lowercase
    # -----------------------------------------------------
    
    if "alert" in df.columns:
        df["alert"] = (
            df["alert"]
            .astype("string")
            .str.strip()
            .str.lower()
            .replace("", pd.NA)
        )
        
    # -----------------------------------------------------
    # TEXT CLEANING
    # -----------------------------------------------------
    
    text_columns = [
        "magType",
        "status",
        "type",
        "net",
        "sources",
        "types"
    ]
    
    for column in text_columns:
        df[column] = (
            df[column]
            .astype("string")
            .str.strip()
            .replace("", pd.NA)
        )

    # -----------------------------------------------------
    # NUMERIC CLEANING
    # -----------------------------------------------------

    numeric_columns = [
        "mag",
        "depth_km",
        "nst",
        "dmin",
        "rms",
        "gap",
        "magError",
        "depthError",
        "magNst",
        "sig"
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )
            
    for column in numeric_columns:
        if column in df.columns:
            median_value = df[column].median()

            if pd.notna(median_value):
                df[column] = df[column].fillna(median_value)
            else:
                df[column] = df[column].fillna(0)

    # -----------------------------------------------------
    # DERIVED DATE COLUMNS
    # -----------------------------------------------------

    df["year"] = df["time"].dt.year.astype("Int64")
    df["month"] = df["time"].dt.month.astype("Int64")
    df["day"] = df["time"].dt.day.astype("Int64")

    # Monday = 0, Sunday = 6
    df["day_of_week"] = df["time"].dt.dayofweek.astype("Int64")

    # -----------------------------------------------------
    # DEPTH CLASSIFICATION
    # -----------------------------------------------------

    def classify_depth(depth):
        if pd.isna(depth):
            return "Unknown"
        if depth < 70:
            return "Shallow"
        if depth < 300:
            return "Intermediate"
        return "Deep"

    df["depth_category"] = df["depth_km"].apply(classify_depth)

    # -----------------------------------------------------
    # MAGNITUDE CLASSIFICATION
    # -----------------------------------------------------

    def classify_magnitude(magnitude):
        if pd.isna(magnitude):
            return "Unknown"
        if magnitude >= 7.0:
            return "Strong"
        if magnitude >= 6.0:
            return "Moderate"
        return "Lower"

    df["strength_flag"] = df["mag"].apply(classify_magnitude)

    # This is a screening flag, not a verified damage assessment.
    df["potentially_destructive"] = (
        df["mag"].ge(6.0).astype("boolean")
    )

    return df


# =========================================================
# 3. MYSQL CONNECTION
# =========================================================

def create_mysql_engine():

    required_variables = [
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE"
    ]

    missing = [
        variable
        for variable in required_variables
        if not os.getenv(variable)
    ]

    if missing:
        raise RuntimeError(
            f"Missing .env variables: {', '.join(missing)}"
        )

    db_url = URL.create(
        drivername="mysql+pymysql",
        username=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE")
    )

    return create_engine(
        db_url,
        pool_pre_ping=True
    )


# =========================================================
# 4. STORE DATA IN MYSQL
# =========================================================

def store_data_in_mysql(df, engine):

    try:
        # Check whether the table exists
        inspector = inspect(engine)

        if inspector.has_table(TABLE_NAME):
            # Remove all existing records
            with engine.begin() as connection:
                connection.execute(
                    text(f"TRUNCATE TABLE `{TABLE_NAME}`")
                )

            print(f"Existing table '{TABLE_NAME}' truncated.")
        else:
            print(f"Table '{TABLE_NAME}' does not exist.")

        # Insert the new DataFrame records
        df.to_sql(
            name=TABLE_NAME,
            con=engine,
            if_exists="append",
            index=False,
            chunksize=1000
        )

        print(f"Successfully inserted {len(df)} records.")

    except Exception as error:
        print(f"Error storing data in MySQL: {error}")
        raise

# =========================================================
# 5. MAIN EXECUTION
# =========================================================

def main():

    engine = None

    try:
        logging.info("Starting earthquake data pipeline...")

        # Fetch
        df = fetch_earthquake_data()
        logging.info("Fetched DataFrame shape: %s", df.shape)

        # Clean and transform
        df = clean_earthquake_data(df)

        # Inspect data
        print("\nDataFrame shape:", df.shape)
        print("\nSample rows:")
        print(df.head())

        print("\nData types:")
        print(df.dtypes)

        print("\nMissing values:")
        print(df.isna().sum())

        # Optional local backup
        df.to_csv(
            "earthquake_data_cleaned.csv",
            index=False
        )

        # Connect and store
        engine = create_mysql_engine()
        store_data_in_mysql(df, engine)

        # Verify saved data
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM earthquakes")
            ).scalar_one()

        print("\nPipeline completed successfully.")
        print("Records stored in MySQL:", count)

    except (RuntimeError, SQLAlchemyError, ValueError) as error:
        logging.exception("Pipeline failed: %s", error)

    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
