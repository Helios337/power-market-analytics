"""
Deterministic synthetic sample data generator.

Produces a long-format CSV at data/sample_energy.csv representing hourly demand,
price, and generation mix for five U.S. balancing authorities over a fixed 90-day
window (2024-01-01 through 2024-03-30 UTC). The data is physically plausible:
- Diurnal and weekly demand patterns
- Region-specific fuel mixes (e.g., CAISO solar, MISO wind, PJM gas)
- Price spikes correlated with scarcity hours
- Generation sums to ~100% share within each region-hour

Run directly:  python src/generate_sample.py
"""
from __future__ import annotations

from pathlib import Path

from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "data" / "sample_energy.csv"

FUEL_TYPES = ["Coal", "Natural Gas", "Nuclear", "Hydro", "Wind", "Solar", "Other"]

REGIONS: dict[str, dict[str, Any]] = {
    "ERCO": {
        "name": "ERCOT",
        "peak_mw": 75_000,
        "base_price": 38.0,
        "price_volatility": 22.0,
        "spike_prob": 0.015,
        "fuel_shares": {"Natural Gas": 0.42, "Wind": 0.25, "Coal": 0.12, "Nuclear": 0.08, "Solar": 0.08, "Hydro": 0.02, "Other": 0.03},
    },
    "PJM": {
        "name": "PJM",
        "peak_mw": 145_000,
        "base_price": 34.0,
        "price_volatility": 12.0,
        "spike_prob": 0.008,
        "fuel_shares": {"Natural Gas": 0.38, "Nuclear": 0.20, "Coal": 0.18, "Wind": 0.12, "Solar": 0.04, "Hydro": 0.04, "Other": 0.04},
    },
    "CISO": {
        "name": "CAISO",
        "peak_mw": 50_000,
        "base_price": 46.0,
        "price_volatility": 16.0,
        "spike_prob": 0.010,
        "fuel_shares": {"Natural Gas": 0.30, "Solar": 0.20, "Hydro": 0.14, "Wind": 0.12, "Nuclear": 0.09, "Coal": 0.03, "Other": 0.12},
    },
    "MISO": {
        "name": "MISO",
        "peak_mw": 90_000,
        "base_price": 31.0,
        "price_volatility": 10.0,
        "spike_prob": 0.006,
        "fuel_shares": {"Natural Gas": 0.36, "Coal": 0.22, "Wind": 0.20, "Nuclear": 0.10, "Hydro": 0.05, "Solar": 0.03, "Other": 0.04},
    },
    "NYIS": {
        "name": "NYISO",
        "peak_mw": 30_000,
        "base_price": 52.0,
        "price_volatility": 14.0,
        "spike_prob": 0.009,
        "fuel_shares": {"Natural Gas": 0.34, "Hydro": 0.20, "Nuclear": 0.18, "Wind": 0.10, "Solar": 0.04, "Coal": 0.04, "Other": 0.10},
    },
}

START = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
END = pd.Timestamp("2024-03-30 23:00:00", tz="UTC")
HOURS = int((END - START).total_seconds() // 3600) + 1


def _diurnal_factor(hours: np.ndarray) -> np.ndarray:
    """Peak around 19:00, trough around 05:00."""
    rad = 2 * np.pi * (hours - 5) / 24
    return 0.78 + 0.22 * np.sin(rad - np.pi / 2)


def _weekly_factor(dates: pd.DatetimeIndex) -> np.ndarray:
    """Weekdays higher than weekends."""
    dow = dates.dayofweek  # 0=Mon, 6=Sun
    return np.where(dow < 5, 1.0, 0.92)


def _solar_shape(hours: np.ndarray) -> np.ndarray:
    """Zero at night, bell curve centered at 13:00."""
    shape = np.maximum(0, np.sin(np.pi * (hours - 6) / 13))
    return np.where((hours >= 6) & (hours <= 19), shape, 0.0)


def _build_region_data(code: str, rng: np.random.Generator) -> pd.DataFrame:
    cfg = REGIONS[code]
    timestamps = pd.date_range(START, periods=HOURS, freq="h", tz="UTC")
    hours = timestamps.hour.values.astype(float)
    days = (timestamps - START).days.values.astype(float)

    diurnal = _diurnal_factor(hours)
    weekly = _weekly_factor(timestamps)
    seasonal = 1.0 + 0.03 * np.sin(2 * np.pi * days / 90)
    ar_noise = np.zeros(HOURS)
    for t in range(1, HOURS):
        ar_noise[t] = 0.7 * ar_noise[t - 1] + rng.normal(0, 0.02)
    demand = cfg["peak_mw"] * diurnal * weekly * seasonal * (1 + ar_noise)
    demand = np.clip(demand, 1_000.0, None)

    # Price: base + demand-driven scarcity + random spikes.
    # Scarcity rises sharply when demand approaches peak, especially in ERCOT.
    scarcity_threshold = 0.85 * cfg["peak_mw"]
    scarcity_coef = cfg["base_price"] * 1.5 / cfg["peak_mw"]
    scarcity = scarcity_coef * np.maximum(0, demand - scarcity_threshold)
    price = cfg["base_price"] + scarcity + rng.normal(0, cfg["price_volatility"], HOURS)
    spikes = rng.random(HOURS) < cfg["spike_prob"]
    price[spikes] *= rng.uniform(3.0, 7.0, size=np.count_nonzero(spikes))
    price = np.maximum(price, 5.0)

    rows = []
    for i, ts in enumerate(timestamps):
        total_demand = demand[i]
        # Generation is demand plus a small system margin (losses/exports).
        total_gen = total_demand * rng.uniform(1.01, 1.03)

        # Build fuel generation with realistic time-of-day variation.
        base_shares = cfg["fuel_shares"].copy()
        solar_gen = base_shares["Solar"] * total_gen * _solar_shape(hours)[i]
        wind_factor = 1.0 + 0.3 * rng.normal(0, 1)
        wind_gen = base_shares["Wind"] * total_gen * max(0, wind_factor)
        nuclear_gen = base_shares["Nuclear"] * total_gen * rng.uniform(0.98, 1.02)
        coal_gen = base_shares["Coal"] * total_gen * rng.uniform(0.90, 1.05)
        hydro_gen = base_shares["Hydro"] * total_gen * rng.uniform(0.70, 1.20)
        gas_gen = base_shares["Natural Gas"] * total_gen * rng.uniform(0.80, 1.25)
        other_gen = base_shares["Other"] * total_gen * rng.uniform(0.80, 1.20)

        fuel_gens = {
            "Solar": solar_gen,
            "Wind": wind_gen,
            "Nuclear": nuclear_gen,
            "Coal": coal_gen,
            "Hydro": hydro_gen,
            "Natural Gas": gas_gen,
            "Other": other_gen,
        }

        # Normalize shares to sum exactly to 100 while preserving relative mix.
        raw_total = sum(fuel_gens.values())
        for fuel in FUEL_TYPES:
            share = 100.0 * fuel_gens[fuel] / raw_total
            rows.append(
                {
                    "timestamp": ts,
                    "region_code": code,
                    "fuel_type": fuel,
                    "demand_mw": round(total_demand, 2),
                    "price_musd_mwh": round(price[i], 4),
                    "generation_mwh": round(total_gen * share / 100.0, 2),
                    "share_percent": round(share, 2),
                }
            )

    return pd.DataFrame(rows)


def generate_sample_csv(seed: int = 42) -> pd.DataFrame:
    """Generate the full deterministic sample dataset and write it to CSV."""
    rng = np.random.default_rng(seed)
    frames = [_build_region_data(code, rng) for code in REGIONS]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["timestamp", "region_code", "fuel_type"]).reset_index(drop=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    return df


if __name__ == "__main__":
    df = generate_sample_csv()
    print(f"Wrote {len(df):,} rows to {OUTPUT_PATH}")
    print(df.groupby("region_code").size())
