#!/usr/bin/env bash
# Purpose: Execute all analytical SQL queries against the warehouse and save
# console-formatted output to the /output/ folder.

set -euo pipefail

OUTPUT_DIR="${PWD}/output"
mkdir -p "${OUTPUT_DIR}"

DEFAULT_PSQL="docker compose exec -T postgres psql -U ${POSTGRES_USER:-energy} -d ${POSTGRES_DB:-energywarehouse} -f /dev/stdin"
PSQL="${PSQL_CMD:-$DEFAULT_PSQL}"

for query_file in queries/q01_running_total.sql \
                  queries/q02_lag_pct_change.sql \
                  queries/q03_top_regions.sql \
                  queries/q04_fuel_mix_pivot.sql \
                  queries/q05_moving_average.sql; do
    name=$(basename "${query_file}" .sql)
    out="${OUTPUT_DIR}/${name}.txt"
    echo "Running ${query_file} ..."
    if ! ${PSQL} < "${query_file}" > "${out}" 2>&1; then
        echo "FAIL: ${query_file}" >&2
        exit 1
    fi
    echo "Wrote ${out}"
done

echo "All queries completed successfully."
