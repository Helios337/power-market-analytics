-- Purpose: Pivot the hourly generation mix into a region-by-quarter matrix using
-- conditional aggregation (FILTER). Values are share of regional generation.

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
ORDER BY region_code, year, quarter;
