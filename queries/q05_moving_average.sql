-- Purpose: Compute a 24-hour moving average of demand per region using a framed
-- window (ROWS BETWEEN 23 PRECEDING AND CURRENT ROW). Rows before a full 24-hour
-- window is available are excluded.

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
LIMIT 20;
