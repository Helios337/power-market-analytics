-- Purpose: Rank regions by their average peak-hour (17:00-21:00 UTC) demand per quarter
-- and surface the top 3 regions in each quarter.

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
ORDER BY year, quarter, rnk;
