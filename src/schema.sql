-- Purpose: Full star-schema DDL for the Energy Demand & Price Analytics Warehouse.
-- Drops existing tables in dependency order, then creates dimensions, facts,
-- constraints, and covering indexes. Safe to rerun.

BEGIN;

-- ---------------------------------------------------------------------------
-- Dimension: time
-- Grain: one row per UTC hour.
-- All calendar attributes are derived from UTC. Analysts requiring BA-local
-- hours should join to dim_region and use ts AT TIME ZONE timezone.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS fact_generation_mix CASCADE;
DROP TABLE IF EXISTS fact_energy_demand CASCADE;
DROP TABLE IF EXISTS dim_fuel CASCADE;
DROP TABLE IF EXISTS dim_region CASCADE;
DROP TABLE IF EXISTS dim_time CASCADE;

CREATE TABLE dim_time (
    time_id     SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL UNIQUE,
    date        DATE NOT NULL,
    hour        SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    day         SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    month       SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    quarter     SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    year        SMALLINT NOT NULL,
    weekday     VARCHAR(10) NOT NULL,
    is_weekend  BOOLEAN NOT NULL,
    CONSTRAINT chk_dim_time_month_day CHECK (
        day >= 1 AND (
            (month IN (1,3,5,7,8,10,12) AND day <= 31) OR
            (month IN (4,6,9,11) AND day <= 30) OR
            (month = 2 AND day <= 28) OR
            (month = 2 AND day = 29
                AND (MOD(year, 4) = 0
                     AND (MOD(year, 100) <> 0 OR MOD(year, 400) = 0)))
        )
    )
);

CREATE INDEX idx_dim_time_ts ON dim_time(ts);
CREATE INDEX idx_dim_time_year_month ON dim_time(year, month);

-- ---------------------------------------------------------------------------
-- Dimension: region
-- Grain: one row per balancing authority / market region.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_region (
    region_id      SERIAL PRIMARY KEY,
    region_code    VARCHAR(10) NOT NULL UNIQUE,
    region_name    VARCHAR(100) NOT NULL,
    country        VARCHAR(50) NOT NULL DEFAULT 'USA',
    timezone       VARCHAR(50) NOT NULL,
    eia_ba_code    VARCHAR(10)
);

CREATE INDEX idx_dim_region_code ON dim_region(region_code);

-- ---------------------------------------------------------------------------
-- Dimension: fuel
-- Grain: one row per fuel type.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_fuel (
    fuel_id       SERIAL PRIMARY KEY,
    fuel_type     VARCHAR(50) NOT NULL UNIQUE,
    is_renewable  BOOLEAN NOT NULL DEFAULT FALSE,
    category      VARCHAR(50) NOT NULL
);

CREATE INDEX idx_dim_fuel_type ON dim_fuel(fuel_type);

-- ---------------------------------------------------------------------------
-- Fact: hourly demand and system price per region
-- Grain: one row per (region, hour).
-- price_usd_mwh is a region-level system price (LMP or demand-weighted price).
-- ---------------------------------------------------------------------------
CREATE TABLE fact_energy_demand (
    demand_id       BIGSERIAL PRIMARY KEY,
    time_id         INT NOT NULL REFERENCES dim_time(time_id),
    region_id       INT NOT NULL REFERENCES dim_region(region_id),
    demand_mw       NUMERIC(12,2) NOT NULL CHECK (demand_mw >= 0),
    price_musd_mwh  NUMERIC(12,4) NOT NULL CHECK (price_musd_mwh >= 0),
    UNIQUE (time_id, region_id)
);

CREATE INDEX idx_fact_energy_demand_time ON fact_energy_demand(time_id);
CREATE INDEX idx_fact_energy_demand_region ON fact_energy_demand(region_id);
CREATE INDEX idx_fact_energy_demand_region_time ON fact_energy_demand(region_id, time_id);
CREATE INDEX idx_fact_energy_demand_region_time_covering
    ON fact_energy_demand(region_id, time_id)
    INCLUDE (demand_mw, price_musd_mwh);

-- ---------------------------------------------------------------------------
-- Fact: hourly generation mix per region and fuel
-- Grain: one row per (region, hour, fuel).
-- share_percent is the share of total regional generation in that hour.
-- ---------------------------------------------------------------------------
CREATE TABLE fact_generation_mix (
    gen_id          BIGSERIAL PRIMARY KEY,
    time_id         INT NOT NULL REFERENCES dim_time(time_id),
    region_id       INT NOT NULL REFERENCES dim_region(region_id),
    fuel_id         INT NOT NULL REFERENCES dim_fuel(fuel_id),
    generation_mwh  NUMERIC(12,2) NOT NULL CHECK (generation_mwh >= 0),
    share_percent   NUMERIC(5,2) NOT NULL CHECK (share_percent BETWEEN 0 AND 100),
    UNIQUE (time_id, region_id, fuel_id)
);

CREATE INDEX idx_fact_generation_mix_time ON fact_generation_mix(time_id);
CREATE INDEX idx_fact_generation_mix_region_fuel ON fact_generation_mix(region_id, fuel_id);
CREATE INDEX idx_fact_generation_mix_region_fuel_time_covering
    ON fact_generation_mix(region_id, fuel_id, time_id)
    INCLUDE (generation_mwh, share_percent);

-- ---------------------------------------------------------------------------
-- Seed static dimensions.
-- ---------------------------------------------------------------------------
INSERT INTO dim_region (region_code, region_name, country, timezone, eia_ba_code) VALUES
    ('ERCO', 'Electric Reliability Council of Texas', 'USA', 'America/Chicago', 'ERCO'),
    ('PJM',  'PJM Interconnection',                 'USA', 'America/New_York', 'PJM'),
    ('CISO', 'California Independent System Operator','USA','America/Los_Angeles','CISO'),
    ('MISO', 'Midcontinent Independent System Operator','USA','America/Chicago','MISO'),
    ('NYIS', 'New York Independent System Operator', 'USA', 'America/New_York', 'NYIS');

INSERT INTO dim_fuel (fuel_type, is_renewable, category) VALUES
    ('Coal',        FALSE, 'fossil'),
    ('Natural Gas', FALSE, 'fossil'),
    ('Nuclear',     FALSE, 'nuclear'),
    ('Hydro',       TRUE,  'renewable'),
    ('Wind',        TRUE,  'renewable'),
    ('Solar',       TRUE,  'renewable'),
    ('Other',       FALSE, 'other');

COMMIT;
