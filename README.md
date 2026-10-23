# Energy Demand & Price Analytics Warehouse

A production-quality, reproducible data warehouse for hourly electricity demand, price, and generation-by-fuel analytics.

Built for a Data Analyst interview portfolio, it demonstrates:
- Star-schema dimensional modeling in PostgreSQL
- Idempotent Python ETL with SQLAlchemy
- Production-grade constraints, indexes, and CI
- Analytical SQL using window functions, CTEs, and conditional aggregation

## Problem Statement

An energy analytics consultancy needs a reproducible pipeline to answer weekly analyst questions:
- Is electricity demand trending?
- Where are prices spiking and why?
- What does the fuel mix look like across markets?

This project ingests hourly data into a PostgreSQL star schema and answers those questions with clean, defensible SQL.

## Tech Stack

- Python 3.11+ with pandas, SQLAlchemy 2.x, psycopg2-binary
- PostgreSQL 16 via Docker
- Docker Compose
- GitHub Actions CI

## Data Source

The pipeline prefers live data from the EIA Open Data API v2.
If `EIA_API_KEY` is not set, it falls back to the bundled deterministic sample CSV at `data/sample_energy.csv`.
This guarantees the pipeline runs in CI and on any machine without registration.

## Schema

```text
+----------------+         +----------------+         +----------------+
|    dim_time    |         |   dim_region   |         |    dim_fuel    |
|----------------|         |----------------|         |----------------|
| time_id   PK   |<-------<| region_id  PK  |>-------<| fuel_id    PK  |
| ts        UQ   |         | region_code UQ |         | fuel_type  UQ  |
| date           |         | region_name    |         | is_renewable   |
| hour           |         | country        |         | category       |
| day            |         | timezone       |         +----------------+
| month          |         | eia_ba_code    |
| quarter        |         +----------------+
| year           |
| weekday        |
| is_weekend     |
+--------+-------+
         |
         |            +-----------------------+
         |            |  fact_energy_demand   |
         |            |-----------------------|
         +----------->| demand_id        PK   |
                      | time_id          FK   |
                      | region_id        FK   |
                      | demand_mw             |
                      | price_musd_mwh        |
                      +-----------------------+

+----------------+      +-------------------------+
|    dim_time    |      |   fact_generation_mix   |
|----------------|      |-------------------------|
| time_id   PK   |<-----| gen_id             PK   |
+----------------+      | time_id            FK   |
                        | region_id          FK   |
                        | fuel_id            FK   |
                        | generation_mwh          |
                        | share_percent           |
                        +-------------------------+
```

Grain:
- `fact_energy_demand`: one row per (region, hour)
- `fact_generation_mix`: one row per (region, hour, fuel)

## Project Structure

```text
power-market-analytics/
├── docker-compose.yml          # postgres:16 service
├── Makefile                    # up, down, seed, verify, clean
├── pyproject.toml              # ruff, mypy, pytest configuration
├── requirements.txt            # pinned runtime dependencies
├── requirements-dev.txt        # pinned dev dependencies (pytest, ruff, mypy)
├── .env.example                # copy to .env and fill EIA_API_KEY optionally
├── src/
│   ├── schema.sql              # full DDL
│   ├── db.py                   # SQLAlchemy engine factory
│   ├── eia_client.py           # optional EIA API client
│   ├── etl.py                  # idempotent ETL
│   └── generate_sample.py      # deterministic sample CSV generator
├── data/
│   └── sample_energy.csv       # bundled fallback data
├── queries/
│   ├── q01_running_total.sql
│   ├── q02_lag_pct_change.sql
│   ├── q03_top_regions.sql
│   ├── q04_fuel_mix_pivot.sql
│   ├── q05_moving_average.sql
│   └── run_all.sh
├── analysis/
│   ├── impact_summary.py
│   └── explore.ipynb
├── tests/
│   └── test_assertions.py
└── .github/workflows/ci.yml
```

## Quick Start

1. Clone or enter the project directory.
2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
3. (Optional) add an EIA API key to `.env` for live data.
4. Start PostgreSQL and install Python dependencies:
   ```bash
   make up
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
5. Build the schema and load data:
   ```bash
   make seed
   ```
6. Run the analytical queries and CI assertions:
   ```bash
   make verify
   ```

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make up` | Start PostgreSQL container |
| `make down` | Stop PostgreSQL container |
| `make wait-db` | Wait for PostgreSQL to be healthy |
| `make schema` | Apply `src/schema.sql` |
| `make seed` | Start DB, apply schema, and run ETL |
| `make verify` | Run queries and CI assertions |
| `make clean` | Remove containers, volumes, and outputs |

## Idempotency Proof

The ETL uses natural-key `UNIQUE` constraints and `INSERT ... ON CONFLICT DO UPDATE`.
Re-running `make seed` does not create duplicates.

Note: `make seed` re-applies `src/schema.sql`, which drops and recreates the
tables, so the unchanged counts after a re-seed are expected. The meaningful
proof is at the ETL level: `test_etl_idempotent` runs `src/etl.py` a second
time against the same schema and asserts that every row count is unchanged.

Real output after two consecutive runs:

```text
fact_energy_demand   : 10800   # first run
fact_generation_mix  : 75600   # first run

fact_energy_demand   : 10800   # second run, no change
fact_generation_mix  : 75600   # second run, no change
```

## Sample Analytical Queries

### 1. Running total of hourly demand per region

See `queries/q01_running_total.sql`:

```sql
SELECT
    dr.region_code,
    dt.ts,
    fed.demand_mw,
    SUM(fed.demand_mw) OVER (
        PARTITION BY fed.region_id
        ORDER BY dt.ts
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_demand_mw
FROM fact_energy_demand fed
JOIN dim_time dt ON dt.time_id = fed.time_id
JOIN dim_region dr ON dr.region_id = fed.region_id
ORDER BY dr.region_code, dt.ts
LIMIT 20;```

### 2. Hour-over-hour price percentage change

See `queries/q02_lag_pct_change.sql`:

```sql
WITH price_lag AS (
    SELECT
        dr.region_code,
        dt.ts,
        fed.price_musd_mwh,
        LAG(fed.price_musd_mwh) OVER (
            PARTITION BY fed.region_id
            ORDER BY dt.ts
        ) AS prev_price_musd_mwh
    FROM fact_energy_demand fed
    JOIN dim_time dt ON dt.time_id = fed.time_id
    JOIN dim_region dr ON dr.region_id = fed.region_id
)
SELECT
    region_code,
    ts,
    ROUND(price_musd_mwh::numeric, 2) AS price_musd_mwh,
    ROUND(COALESCE(prev_price_musd_mwh, price_musd_mwh)::numeric, 2) AS prev_price_musd_mwh,
    ROUND(
        100.0 * (price_musd_mwh - COALESCE(prev_price_musd_mwh, price_musd_mwh))
        / NULLIF(COALESCE(prev_price_musd_mwh, price_musd_mwh), 0)
    , 2) AS price_pct_change
FROM price_lag
ORDER BY region_code, ts
LIMIT 20;```

### 3. Top-3 regions by average peak-hour demand per quarter

See `queries/q03_top_regions.sql`:

```sql
WITH peak_hour_demand AS (
    SELECT
        dr.region_id,
        dr.region_code,
        dt.year,
        dt.quarter,
        fed.demand_mw
    FROM fact_energy_demand fed
    JOIN dim_time dt ON dt.time_id = fed.time_id
    JOIN dim_region dr ON dr.region_id = fed.region_id
    WHERE dt.hour BETWEEN 17 AND 21
),
avg_peak AS (
    SELECT
        region_code,
        year,
        quarter,
        ROUND(AVG(demand_mw)::numeric, 2) AS avg_peak_demand_mw
    FROM peak_hour_demand
    GROUP BY region_code, year, quarter
),
ranked AS (
    SELECT
        *,
        RANK() OVER (
            PARTITION BY year, quarter
            ORDER BY avg_peak_demand_mw DESC
        ) AS rnk
    FROM avg_peak
)
SELECT
    year,
    quarter,
    region_code,
    avg_peak_demand_mw,
    rnk
FROM ranked
WHERE rnk <= 3
ORDER BY year, quarter, rnk;```

### 4. Fuel mix pivot by region and quarter

See `queries/q04_fuel_mix_pivot.sql`:

```sql
WITH regional_quarterly_generation AS (
    SELECT
        dr.region_code,
        dt.year,
        dt.quarter,
        df.fuel_type,
        fgm.generation_mwh
    FROM fact_generation_mix fgm
    JOIN dim_time dt ON dt.time_id = fgm.time_id
    JOIN dim_region dr ON dr.region_id = fgm.region_id
    JOIN dim_fuel df ON df.fuel_id = fgm.fuel_id
)
SELECT
    region_code,
    year,
    quarter,
    ROUND(SUM(generation_mwh)::numeric, 2) AS total_generation_mwh,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Coal')
        / NULLIF(SUM(generation_mwh), 0), 2) AS coal_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Natural Gas')
        / NULLIF(SUM(generation_mwh), 0), 2) AS natural_gas_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Nuclear')
        / NULLIF(SUM(generation_mwh), 0), 2) AS nuclear_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Hydro')
        / NULLIF(SUM(generation_mwh), 0), 2) AS hydro_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Wind')
        / NULLIF(SUM(generation_mwh), 0), 2) AS wind_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Solar')
        / NULLIF(SUM(generation_mwh), 0), 2) AS solar_pct,
    ROUND(100.0 * SUM(generation_mwh) FILTER (WHERE fuel_type = 'Other')
        / NULLIF(SUM(generation_mwh), 0), 2) AS other_pct
FROM regional_quarterly_generation
GROUP BY region_code, year, quarter
ORDER BY region_code, year, quarter;```

### 5. 24-hour moving average of demand

See `queries/q05_moving_average.sql`:

```sql
WITH ma AS (
    SELECT
        dr.region_code,
        dt.ts,
        fed.demand_mw,
        AVG(fed.demand_mw) OVER (
            PARTITION BY fed.region_id
            ORDER BY dt.ts
            ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
        ) AS demand_ma_24h
    FROM fact_energy_demand fed
    JOIN dim_time dt ON dt.time_id = fed.time_id
    JOIN dim_region dr ON dr.region_id = fed.region_id
)
SELECT
    region_code,
    ts,
    ROUND(demand_mw::numeric, 2) AS demand_mw,
    ROUND(demand_ma_24h::numeric, 2) AS demand_ma_24h
FROM ma
WHERE ts >= (SELECT MIN(ts) FROM dim_time) + INTERVAL '23 hours'
ORDER BY region_code, ts
LIMIT 20;```

## Key Finding

```text
[FINDING 1] ERCOT peak-hour prices (17:00-21:00 UTC) averaged $46.07/MWh vs off-peak (00:00-06:00 UTC) $41.02/MWh -- a 12.3% premium.
```

This quantifies the scarcity premium during high-demand evening hours in Texas and is a defensible talking point in an analyst interview.

## Real Query Outputs

Captured from a clean `make clean && make up && make seed && make verify` run.

### Q1 - Running total of hourly demand (excerpt)

```text
 region_code |           ts           | demand_mw | cumulative_demand_mw
-------------+------------------------+-----------+----------------------
 CISO        | 2024-01-01 00:00:00+00 |  36152.99 |             36152.99
 CISO        | 2024-01-01 01:00:00+00 |  32809.29 |             68962.28
 CISO        | 2024-01-01 02:00:00+00 |  30689.25 |             99651.53
 CISO        | 2024-01-01 03:00:00+00 |  29574.22 |            129225.75
 CISO        | 2024-01-01 04:00:00+00 |  27843.02 |            157068.77
 CISO        | 2024-01-01 05:00:00+00 |  28120.61 |            185189.38
```

### Q2 - Hour-over-hour price percentage change (excerpt)

```text
 region_code |           ts           | price_musd_mwh | prev_price_musd_mwh | price_pct_change
-------------+------------------------+----------------+---------------------+------------------
 CISO        | 2024-01-01 00:00:00+00 |          32.07 |               32.07 |             0.00
 CISO        | 2024-01-01 01:00:00+00 |          43.06 |               32.07 |            34.27
 CISO        | 2024-01-01 02:00:00+00 |          16.60 |               43.06 |           -61.44
```

### Q3 - Top-3 regions by average peak-hour demand per quarter

```text
 year | quarter | region_code | avg_peak_demand_mw | rnk
------+---------+-------------+--------------------+-----
 2024 |       1 | PJM         |          135791.25 |   1
 2024 |       1 | MISO        |           84272.60 |   2
 2024 |       1 | ERCO        |           70062.67 |   3
```

### Q4 - Fuel mix pivot by region and quarter

```text
 region_code | year | quarter | total_generation_mwh | coal_pct | natural_gas_pct | nuclear_pct | hydro_pct | wind_pct | solar_pct | other_pct
-------------+------+---------+----------------------+----------+-----------------+-------------+-----------+----------+-----------+-----------
 CISO        | 2024 |       1 |          84090709.51 |     3.39 |           35.45 |       10.41 |     15.38 |    13.76 |      7.77 |     13.84
 ERCO        | 2024 |       1 |         125651109.97 |    12.32 |           45.23 |        8.43 |      2.00 |    25.83 |      3.01 |      3.17
 MISO        | 2024 |       1 |         151042650.95 |    21.99 |           37.56 |       10.26 |      4.86 |    20.09 |      1.13 |      4.12
 NYIS        | 2024 |       1 |          50371911.00 |     4.03 |           35.78 |       18.58 |     19.63 |    10.18 |      1.50 |     10.30
 PJM         | 2024 |       1 |         243965661.58 |    18.02 |           39.84 |       20.57 |      3.91 |    12.06 |      1.50 |      4.09
```

### Q5 - 24-hour moving average of demand (excerpt)

```text
 region_code |           ts           | demand_mw | demand_ma_24h
-------------+------------------------+-----------+---------------
 CISO        | 2024-01-01 23:00:00+00 |  38205.55 |      39107.14
 CISO        | 2024-01-02 00:00:00+00 |  35603.16 |      39084.23
 CISO        | 2024-01-02 01:00:00+00 |  32999.19 |      39092.14
 CISO        | 2024-01-02 02:00:00+00 |  30947.67 |      39102.91
 CISO        | 2024-01-02 03:00:00+00 |  28420.80 |      39054.85
```

## CI

GitHub Actions runs on every push:
1. Spins up a PostgreSQL 16 service container.
2. Lints with Ruff and type-checks `src/` with mypy.
3. Applies `src/schema.sql`.
4. Runs `src/etl.py`.
5. Executes all five queries via `queries/run_all.sh`.
6. Runs the test suite with `pytest tests/ -v` to verify counts, constraints,
   idempotency, and the ETL's pure transforms (shares, calendar, cleaning,
   EIA overlay).

## License

This project is provided as a portfolio demonstration.
