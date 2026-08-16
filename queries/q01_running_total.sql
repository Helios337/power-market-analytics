-- Purpose: Compute the cumulative hourly demand per region using a windowed SUM.
-- The running total resets per region and advances chronologically by hour.

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
LIMIT 20;
