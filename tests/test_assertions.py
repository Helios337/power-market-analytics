"""
CI-style assertions for the warehouse.

Run with pytest after `make seed`:
    pytest tests/ -v

Verifies row counts, constraints, query outputs, and ETL idempotency.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import get_engine
import etl

EXPECTED = {
    "dim_time": 2160,
    "dim_region": 5,
    "dim_fuel": 7,
    "fact_energy_demand": 10_800,
    "fact_generation_mix": 75_600,
}


def _count(table: str) -> int:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


# ---------------------------------------------------------------------------
# Pure ETL unit checks (no database required)
# ---------------------------------------------------------------------------

def test_recompute_shares_sum_to_100() -> None:
    """Recomputed fuel shares must stay in [0, 100] and sum to 100% per region-hour."""
    df = pd.DataFrame(
        {
            "timestamp": ["2024-01-01 00:00:00+00:00"] * 2
            + ["2024-01-01 01:00:00+00:00"] * 2,
            "region_code": ["ERCO"] * 4,
            "fuel_type": ["Coal", "Wind", "Coal", "Wind"],
            "generation_mwh": [300.0, 100.0, 100.0, 100.0],
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    out = etl._recompute_shares(df)
    assert out["share_percent"].between(0, 100).all(), "shares outside [0,100]"
    totals = out.groupby("timestamp")["share_percent"].sum()
    assert ((totals - 100).abs() < 0.01).all(), f"shares do not sum to 100: {totals.to_dict()}"
    assert out.loc[0, "share_percent"] == 75.0, "Coal should hold 300/400 = 75%"
    print("PASS: recomputed shares stay in [0,100] and sum to 100%")


def test_build_dim_time_contiguous_hourly() -> None:
    """The calendar dimension must be complete, hourly, and monotonic."""
    raw = etl.load_sample_csv()
    cleaned = etl.clean_and_validate(raw)
    dim = etl.build_dim_time(cleaned)
    expected = int(
        (cleaned["timestamp"].max() - cleaned["timestamp"].min()).total_seconds() / 3600 + 1
    )
    assert len(dim) == expected, f"expected {expected} hourly rows, got {len(dim)}"
    assert dim["ts"].is_monotonic_increasing
    assert dim["ts"].is_unique
    assert dim["hour"].between(0, 23).all()
    assert dim["day"].between(1, 31).all()
    assert dim["month"].between(1, 12).all()
    print(f"PASS: dim_time has {len(dim)} contiguous hourly rows")


def test_clean_and_validate_enforces_bounds() -> None:
    """Cleaning must drop null rows, drop negative rows, and clip extreme outliers."""
    timestamps = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "region_code": ["ERCO"] * 5,
            "fuel_type": ["Coal"] * 5,
            "demand_mw": [None, -10.0, 100.0, 1e9, 200.0],   # null, negative, ok, outlier, ok
            "price_musd_mwh": [10.0, 10.0, 20.0, 30.0, 40.0],
            "generation_mwh": [5.0, 5.0, 7.0, 8.0, -9.0],   # last row dropped for negative gen
            "share_percent": [50.0, 50.0, 70.0, 80.0, 90.0],
        }
    )
    out = etl.clean_and_validate(df)
    assert len(out) == 2, f"expected 2 clean rows, got {len(out)}"
    assert (out["demand_mw"] >= 0).all()
    assert (out["price_musd_mwh"] >= 0).all()
    assert (out["generation_mwh"] >= 0).all()
    assert (out["share_percent"] >= 0).all() and (out["share_percent"] <= 100).all()
    assert out["demand_mw"].max() < 1e9, "outlier demand must be clipped below its raw 1e9 value"
    print(f"PASS: cleaning dropped {len(df) - len(out)} row(s), enforced bounds, and clipped outliers")


def test_overlay_eia_falls_back_without_key() -> None:
    """Without an API key, _overlay_eia must return the sample unchanged."""
    sample = etl.load_sample_csv().head(50)
    before = sample.copy()
    old_key = os.environ.pop("EIA_API_KEY", None)
    try:
        out = etl._overlay_eia(sample, sample["timestamp"].min(), sample["timestamp"].max())
    finally:
        if old_key is not None:
            os.environ["EIA_API_KEY"] = old_key
    pd.testing.assert_frame_equal(out, before)
    print("PASS: no-key overlay is a no-op")


def test_overlay_eia_applies_live_data() -> None:
    """With EIA data present, matching keys must be overlaid; missing ones fall back."""
    ts = ["2024-01-01 00:00:00+00:00", "2024-01-01 00:00:00+00:00"]
    sample = pd.DataFrame(
        {
            "timestamp": ts * 1,
            "region_code": ["ERCO", "ERCO"],
            "fuel_type": ["Coal", "Wind"],
            "demand_mw": [100.0, 100.0],
            "price_musd_mwh": [10.0, 10.0],
            "generation_mwh": [50.0, 50.0],
            "share_percent": [50.0, 50.0],
        }
    )
    sample["timestamp"] = pd.to_datetime(sample["timestamp"], utc=True)

    eia_demand = pd.DataFrame(
        {"timestamp": ts[:1], "region_code": ["ERCO"], "demand_mw": [999.0]}
    )
    eia_price = pd.DataFrame(
        {"timestamp": ts[:1], "region_code": ["ERCO"], "price_musd_mwh": [88.0]}
    )
    eia_gen = pd.DataFrame(
        {
            "timestamp": ts[:1],
            "region_code": ["ERCO"],
            "fuel_type": ["Coal"],
            "generation_mwh": [444.0],
        }
    )
    eia_demand["timestamp"] = pd.to_datetime(eia_demand["timestamp"], utc=True)
    eia_price["timestamp"] = pd.to_datetime(eia_price["timestamp"], utc=True)
    eia_gen["timestamp"] = pd.to_datetime(eia_gen["timestamp"], utc=True)

    originals = (etl.fetch_hourly_demand, etl.fetch_hourly_generation, etl.fetch_hourly_price)
    old_key = os.environ.pop("EIA_API_KEY", None)
    try:
        os.environ["EIA_API_KEY"] = "test-key"
        etl.fetch_hourly_demand = lambda *a, **k: eia_demand
        etl.fetch_hourly_generation = lambda *a, **k: eia_gen
        etl.fetch_hourly_price = lambda *a, **k: eia_price
        out = etl._overlay_eia(sample, sample["timestamp"].min(), sample["timestamp"].max())
    finally:
        (
            etl.fetch_hourly_demand,
            etl.fetch_hourly_generation,
            etl.fetch_hourly_price,
        ) = originals
        if old_key is not None:
            os.environ["EIA_API_KEY"] = old_key
        else:
            os.environ.pop("EIA_API_KEY", None)

    coal = out[(out["fuel_type"] == "Coal")].iloc[0]
    wind = out[(out["fuel_type"] == "Wind")].iloc[0]
    assert coal["demand_mw"] == 999.0, "EIA demand should overlay sample demand"
    assert coal["price_musd_mwh"] == 88.0, "EIA price should overlay sample price"
    assert coal["generation_mwh"] == 444.0, "EIA coal generation should overlay"
    assert wind["generation_mwh"] == 50.0, "missing EIA fuel should keep sample value"
    print("PASS: live overlay replaces matching keys and preserves the rest")


@pytest.mark.skipif(
    os.getenv("EIA_API_KEY") is not None,
    reason="exact counts are sample-specific; a live EIA overlay changes them",
)
def test_counts() -> None:
    """Assert exact expected row counts for each table."""
    for table, expected in EXPECTED.items():
        actual = _count(table)
        assert actual == expected, f"{table}: expected {expected}, got {actual}"
        print(f"PASS: {table} = {actual}")


def test_share_constraint() -> None:
    """Generation shares must sum to ~100% within each region-hour."""
    engine = get_engine()
    sql = """
        SELECT COUNT(*) FROM (
            SELECT time_id, region_id
            FROM fact_generation_mix
            GROUP BY time_id, region_id
            HAVING SUM(share_percent) NOT BETWEEN 99.5 AND 100.5
        ) v
    """
    with engine.connect() as conn:
        violations = conn.execute(text(sql)).scalar_one()
    assert violations == 0, f"Found {violations} region-hours with share sums outside 99.5-100.5"
    print(f"PASS: share_percent sums OK ({violations} violations)")

def test_query_outputs_exist() -> None:
    """All query output files must be non-empty."""
    output_dir = Path(__file__).resolve().parents[1] / "output"
    for i in range(1, 6):
        files = list(output_dir.glob(f"q{i:02d}_*.txt"))
        assert files, f"Missing output file for query {i}"
        for f in files:
            assert f.stat().st_size > 100, f"Output file {f} is suspiciously small"
            text_content = f.read_text()
            assert "(0 rows)" not in text_content, f"Query {i} returned zero rows"
    print("PASS: all query outputs are non-empty")


def test_etl_idempotent() -> None:
    """Rerunning the ETL must not change row counts."""
    before = {t: _count(t) for t in EXPECTED}
    result = subprocess.run(
        [sys.executable, "src/etl.py"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ETL re-run failed:\n{result.stderr}"
    after = {t: _count(t) for t in EXPECTED}
    assert before == after, f"ETL is not idempotent: before={before}, after={after}"
    print("PASS: ETL is idempotent")
