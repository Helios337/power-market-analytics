"""
Idempotent ETL pipeline for the Energy Demand & Price Analytics Warehouse.

Pipeline steps:
1. Load the bundled sample CSV (deterministic fallback).
2. If EIA_API_KEY is set, overlay live EIA demand/generation/price data.
3. Clean, validate, and clip outliers.
4. Build a complete dim_time calendar.
5. Upsert dimensions and facts with ON CONFLICT DO UPDATE (idempotent + self-correcting).
6. Run sanity checks and print a summary report.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from db import get_engine
from eia_client import fetch_hourly_demand, fetch_hourly_generation, fetch_hourly_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "data" / "sample_energy.csv"

DEMAND_QUANTILE_UPPER = 0.995
PRICE_QUANTILE_UPPER = 0.999


def load_sample_csv(path: Path = SAMPLE_PATH) -> pd.DataFrame:
    """Read the deterministic sample CSV."""
    logger.info("Loading sample CSV from %s", path)
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _overlay_eia(
    sample: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Optionally overlay live EIA data on top of the sample CSV."""
    if not os.getenv("EIA_API_KEY"):
        logger.info("EIA_API_KEY not set; using sample CSV only")
        return sample

    regions = sample["region_code"].unique().tolist()
    df = sample.copy()

    for region in regions:
        # Demand overlay
        logger.info("Fetching EIA demand for %s", region)
        demand = fetch_hourly_demand(region, start, end)
        if not demand.empty:
            df = df.merge(
                demand.rename(columns={"demand_mw": "demand_mw_eia"}),
                on=["timestamp", "region_code"],
                how="left",
            )
            df["demand_mw"] = df["demand_mw_eia"].fillna(df["demand_mw"])
            df = df.drop(columns=["demand_mw_eia"])

        # Price overlay
        logger.info("Fetching EIA price for %s", region)
        price = fetch_hourly_price(region, start, end)
        if not price.empty:
            df = df.merge(
                price.rename(columns={"price_musd_mwh": "price_musd_mwh_eia"}),
                on=["timestamp", "region_code"],
                how="left",
            )
            df["price_musd_mwh"] = df["price_musd_mwh_eia"].fillna(df["price_musd_mwh"])
            df = df.drop(columns=["price_musd_mwh_eia"])

        # Generation overlay: replace only fuels that EIA returns.
        logger.info("Fetching EIA generation for %s", region)
        gen = fetch_hourly_generation(region, start, end)
        if not gen.empty:
            df = df.merge(
                gen.rename(columns={"generation_mwh": "generation_mwh_eia"}),
                on=["timestamp", "region_code", "fuel_type"],
                how="left",
            )
            df["generation_mwh"] = df["generation_mwh_eia"].fillna(df["generation_mwh"])
            df = df.drop(columns=["generation_mwh_eia"])

    return df


def clean_and_validate(df: pd.DataFrame) -> pd.DataFrame:
    """Drop nulls, dedupe, type-cast, and clip extreme outliers."""
    logger.info("Cleaning raw data")
    required = [
        "timestamp",
        "region_code",
        "fuel_type",
        "demand_mw",
        "price_musd_mwh",
        "generation_mwh",
        "share_percent",
    ]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Required column {col} missing from source data")

    df = df.dropna(subset=required)
    df = df.drop_duplicates(subset=["timestamp", "region_code", "fuel_type"])

    numeric_cols = ["demand_mw", "price_musd_mwh", "generation_mwh", "share_percent"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=numeric_cols)

    # Enforce physical bounds.
    df = df[df["demand_mw"] >= 0]
    df = df[df["price_musd_mwh"] >= 0]
    df = df[df["generation_mwh"] >= 0]
    df = df[(df["share_percent"] >= 0) & (df["share_percent"] <= 100)]

    # Clip extreme outliers while preserving the structure of real spikes.
    demand_cap = df["demand_mw"].quantile(DEMAND_QUANTILE_UPPER)
    price_cap = df["price_musd_mwh"].quantile(PRICE_QUANTILE_UPPER)
    df["demand_mw"] = df["demand_mw"].clip(upper=float(demand_cap))
    df["price_musd_mwh"] = df["price_musd_mwh"].clip(upper=float(price_cap))

    return df.reset_index(drop=True)


def build_dim_time(df: pd.DataFrame) -> pd.DataFrame:
    """Generate a complete hourly calendar dimension from the data range."""
    start = df["timestamp"].min().floor("h")
    end = df["timestamp"].max().floor("h")
    timestamps = pd.date_range(start, end, freq="h", tz="UTC")

    dim = pd.DataFrame({"ts": timestamps})
    dim["date"] = dim["ts"].dt.date
    dim["hour"] = dim["ts"].dt.hour.astype(np.int16)
    dim["day"] = dim["ts"].dt.day.astype(np.int16)
    dim["month"] = dim["ts"].dt.month.astype(np.int16)
    dim["quarter"] = dim["ts"].dt.quarter.astype(np.int16)
    dim["year"] = dim["ts"].dt.year.astype(np.int16)
    dim["weekday"] = dim["ts"].dt.day_name()
    dim["is_weekend"] = dim["ts"].dt.weekday >= 5
    return dim


def _upsert_dim_time(engine: Engine, dim_time: pd.DataFrame) -> None:
    """Upsert complete calendar into dim_time."""
    logger.info("Upserting dim_time (%s rows)", len(dim_time))
    dim_time.to_sql("_staging_dim_time", engine, if_exists="replace", index=False)
    sql = """
        INSERT INTO dim_time (ts, date, hour, day, month, quarter, year, weekday, is_weekend)
        SELECT ts, date, hour, day, month, quarter, year, weekday, is_weekend
        FROM _staging_dim_time
        ON CONFLICT (ts) DO UPDATE SET
            date = EXCLUDED.date,
            hour = EXCLUDED.hour,
            day = EXCLUDED.day,
            month = EXCLUDED.month,
            quarter = EXCLUDED.quarter,
            year = EXCLUDED.year,
            weekday = EXCLUDED.weekday,
            is_weekend = EXCLUDED.is_weekend;
        DROP TABLE _staging_dim_time;
    """
    with engine.begin() as conn:
        conn.execute(text(sql))


def _upsert_fact_energy_demand(engine: Engine, df: pd.DataFrame) -> int:
    """Upsert hourly demand/price facts."""
    logger.info("Upserting fact_energy_demand")
    demand = (
        df.groupby(["timestamp", "region_code"], as_index=False)
        .agg({"demand_mw": "mean", "price_musd_mwh": "mean"})
        .reset_index(drop=True)
    )
    demand.to_sql("_staging_demand", engine, if_exists="replace", index=False)

    insert_sql = """
        INSERT INTO fact_energy_demand (time_id, region_id, demand_mw, price_musd_mwh)
        SELECT dt.time_id, dr.region_id, sd.demand_mw, sd.price_musd_mwh
        FROM _staging_demand sd
        JOIN dim_time dt ON dt.ts = sd.timestamp
        JOIN dim_region dr ON dr.region_code = sd.region_code
        ON CONFLICT (time_id, region_id) DO UPDATE SET
            demand_mw = EXCLUDED.demand_mw,
            price_musd_mwh = EXCLUDED.price_musd_mwh
    """
    with engine.begin() as conn:
        conn.execute(text(insert_sql))

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM fact_energy_demand")).scalar_one()

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE _staging_demand"))
    return int(count)


def _recompute_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure share_percent within each region-hour sums to 100."""
    totals = df.groupby(["timestamp", "region_code"])["generation_mwh"].transform("sum")
    df["share_percent"] = (100.0 * df["generation_mwh"] / totals).replace([np.inf, -np.inf], 0).fillna(0)
    df["share_percent"] = df["share_percent"].clip(0, 100).round(2)
    return df


def _upsert_fact_generation_mix(engine: Engine, df: pd.DataFrame) -> int:
    """Upsert hourly generation mix facts."""
    logger.info("Upserting fact_generation_mix")
    df = _recompute_shares(df)
    gen = df[["timestamp", "region_code", "fuel_type", "generation_mwh", "share_percent"]].copy()
    gen.to_sql("_staging_gen", engine, if_exists="replace", index=False)

    insert_sql = """
        INSERT INTO fact_generation_mix (time_id, region_id, fuel_id, generation_mwh, share_percent)
        SELECT dt.time_id, dr.region_id, df.fuel_id, sg.generation_mwh, sg.share_percent
        FROM _staging_gen sg
        JOIN dim_time dt ON dt.ts = sg.timestamp
        JOIN dim_region dr ON dr.region_code = sg.region_code
        JOIN dim_fuel df ON df.fuel_type = sg.fuel_type
        ON CONFLICT (time_id, region_id, fuel_id) DO UPDATE SET
            generation_mwh = EXCLUDED.generation_mwh,
            share_percent = EXCLUDED.share_percent
    """
    with engine.begin() as conn:
        conn.execute(text(insert_sql))

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM fact_generation_mix")).scalar_one()

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE _staging_gen"))
    return int(count)


def run_sanity_checks(engine: Engine) -> dict[str, Any]:
    """Run SELECT COUNT sanity checks and return results."""
    logger.info("Running sanity checks")
    checks = {
        "dim_time": "SELECT COUNT(*) FROM dim_time",
        "dim_region": "SELECT COUNT(*) FROM dim_region",
        "dim_fuel": "SELECT COUNT(*) FROM dim_fuel",
        "fact_energy_demand": "SELECT COUNT(*) FROM fact_energy_demand",
        "fact_generation_mix": "SELECT COUNT(*) FROM fact_generation_mix",
        "date_range": "SELECT MIN(ts), MAX(ts) FROM dim_time",
        "share_sum_violations": """
            SELECT COUNT(*) FROM (
                SELECT time_id, region_id, SUM(share_percent) AS total
                FROM fact_generation_mix
                GROUP BY time_id, region_id
                HAVING SUM(share_percent) NOT BETWEEN 99.5 AND 100.5
            ) v
        """,
    }
    results: dict[str, Any] = {}
    with engine.connect() as conn:
        for name, sql in checks.items():
            row = conn.execute(text(sql)).fetchone()
            results[name] = row[0] if row else None
    return results


def print_summary(results: dict[str, Any]) -> None:
    """Print a formatted summary report."""
    logger.info("=" * 60)
    logger.info("ETL SUMMARY")
    logger.info("=" * 60)
    for key, value in results.items():
        logger.info("%-25s: %s", key, value)
    logger.info("=" * 60)


def main() -> None:
    engine = get_engine(echo=False)

    raw = load_sample_csv()
    start = raw["timestamp"].min()
    end = raw["timestamp"].max()

    enriched = _overlay_eia(raw, start, end)
    cleaned = clean_and_validate(enriched)
    dim_time = build_dim_time(cleaned)

    _upsert_dim_time(engine, dim_time)
    _upsert_fact_energy_demand(engine, cleaned)
    _upsert_fact_generation_mix(engine, cleaned)

    results = run_sanity_checks(engine)
    print_summary(results)


if __name__ == "__main__":
    main()
