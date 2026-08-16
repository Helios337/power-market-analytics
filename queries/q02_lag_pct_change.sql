-- Purpose: Calculate the hour-over-hour percentage change in system price per region.
-- Uses LAG to access the prior hour's price; the first hour of each region is
-- reported with 0% change because no earlier price exists.

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
LIMIT 20;
