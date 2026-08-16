"""
CI-style assertions for the warehouse.

Run after `make seed` to verify row counts, constraints, query outputs,
and ETL idempotency.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import get_engine

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
        path = output_dir / f"q{i:02d}_*.txt"
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


def main() -> int:
    tests = [test_counts, test_share_constraint, test_query_outputs_exist, test_etl_idempotent]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"ERROR: {test.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print("\nAll assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
