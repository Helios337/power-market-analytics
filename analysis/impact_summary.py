"""
Compute one concrete, data-backed talking point from the warehouse.

The insight compares peak-hour (17:00-21:00 UTC) prices against off-peak
(00:00-06:00 UTC) prices in ERCOT and reports the percentage premium.
"""
from __future__ import annotations

from sqlalchemy import text

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import get_engine


def compute_peak_premium(region_code: str = "ERCO") -> dict[str, float]:
    """Return peak and off-peak average prices plus the percentage premium."""
    engine = get_engine()
    sql = """
        SELECT
            ROUND(AVG(fed.price_musd_mwh) FILTER (WHERE dt.hour BETWEEN 17 AND 21)::numeric, 2) AS peak_price,
            ROUND(AVG(fed.price_musd_mwh) FILTER (WHERE dt.hour BETWEEN 0 AND 6)::numeric, 2) AS off_peak_price
        FROM fact_energy_demand fed
        JOIN dim_time dt ON dt.time_id = fed.time_id
        JOIN dim_region dr ON dr.region_id = fed.region_id
        WHERE dr.region_code = :region_code
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"region_code": region_code}).fetchone()

    peak = float(row.peak_price)
    off_peak = float(row.off_peak_price)
    premium = round(100.0 * (peak - off_peak) / off_peak, 1)
    return {
        "region_code": region_code,
        "peak_price": peak,
        "off_peak_price": off_peak,
        "premium_pct": premium,
    }


def print_summary() -> None:
    result = compute_peak_premium("ERCO")
    print(
        f"[FINDING 1] ERCOT peak-hour prices (17:00-21:00 UTC) averaged "
        f"${result['peak_price']:.2f}/MWh vs off-peak (00:00-06:00 UTC) "
        f"${result['off_peak_price']:.2f}/MWh -- a {result['premium_pct']:.1f}% premium."
    )


if __name__ == "__main__":
    print_summary()
