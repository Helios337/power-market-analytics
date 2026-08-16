"""
Optional EIA Open Data API v2 client.

If EIA_API_KEY is present, the ETL will overlay live demand and generation data
on top of the bundled sample CSV. If any EIA call fails or returns no rows, the
ETL silently falls back to the sample data for that metric/region.

Reference endpoints:
- Hourly demand: /electricity/rto/region-data/
- Hourly generation by fuel: /electricity/rto/fuel-type-data/
- Hourly wholesale price: /electricity/rto/wholesale-spot-price/
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.eia.gov/v2"
FUEL_MAP = {
    "COL": "Coal",
    "NG": "Natural Gas",
    "NUC": "Nuclear",
    "HYC": "Hydro",
    "SUN": "Solar",
    "WND": "Wind",
    "OTH": "Other",
}


def _api_key() -> str | None:
    return os.getenv("EIA_API_KEY")


def _paginated_get(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch all paginated rows from an EIA v2 endpoint."""
    key = _api_key()
    if not key:
        return []

    params = {**params, "api_key": key, "offset": 0}
    rows: list[dict[str, Any]] = []
    limit = 5000

    while True:
        params["offset"] = len(rows)
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("EIA request failed: %s", exc)
            return []

        payload = resp.json().get("response", {})
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []

        rows.extend(data)
        total = payload.get("total", len(rows))
        if len(data) < limit or len(rows) >= total:
            break

    return rows


def fetch_hourly_demand(
    ba_code: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Return DataFrame with columns [timestamp, region_code, demand_mw]."""
    url = f"{BASE_URL}/electricity/rto/region-data/data/"
    params = {
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": ba_code,
        "facets[type][]": "D",
        "start": start.strftime("%Y-%m-%dT%H"),
        "end": end.strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }
    rows = _paginated_get(url, params)
    if not rows:
        return pd.DataFrame(columns=["timestamp", "region_code", "demand_mw"])

    df = pd.DataFrame(rows)
    df = df.rename(columns={"period": "timestamp", "value": "demand_mw", "respondent": "region_code"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["demand_mw"] = pd.to_numeric(df["demand_mw"], errors="coerce")
    return df[["timestamp", "region_code", "demand_mw"]].dropna()


def fetch_hourly_generation(
    ba_code: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Return DataFrame with columns [timestamp, region_code, fuel_type, generation_mwh]."""
    url = f"{BASE_URL}/electricity/rto/fuel-type-data/data/"
    params = {
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": ba_code,
        "start": start.strftime("%Y-%m-%dT%H"),
        "end": end.strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }
    rows = _paginated_get(url, params)
    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "region_code", "fuel_type", "generation_mwh"]
        )

    df = pd.DataFrame(rows)
    df = df.rename(
        columns={
            "period": "timestamp",
            "value": "generation_mwh",
            "respondent": "region_code",
            "fueltype": "eia_fuel",
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["generation_mwh"] = pd.to_numeric(df["generation_mwh"], errors="coerce")
    df["fuel_type"] = df["eia_fuel"].map(FUEL_MAP)
    df = df.dropna(subset=["fuel_type", "generation_mwh"])
    return df[["timestamp", "region_code", "fuel_type", "generation_mwh"]]


def fetch_hourly_price(
    ba_code: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Return DataFrame with columns [timestamp, region_code, price_musd_mwh]."""
    url = f"{BASE_URL}/electricity/rto/wholesale-spot-price/data/"
    params = {
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": ba_code,
        "start": start.strftime("%Y-%m-%dT%H"),
        "end": end.strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }
    rows = _paginated_get(url, params)
    if not rows:
        return pd.DataFrame(columns=["timestamp", "region_code", "price_musd_mwh"])

    df = pd.DataFrame(rows)
    df = df.rename(
        columns={"period": "timestamp", "value": "price_musd_mwh", "respondent": "region_code"}
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["price_musd_mwh"] = pd.to_numeric(df["price_musd_mwh"], errors="coerce")
    return df[["timestamp", "region_code", "price_musd_mwh"]].dropna()
