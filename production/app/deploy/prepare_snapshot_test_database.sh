#!/usr/bin/env bash
set -Eeuo pipefail

readonly TEST_DATABASE="tfm_energia_test"
readonly REPO_ROOT="${1:-/home/ubuntu/scripts}"
readonly SCHEMA_FILE="$REPO_ROOT/production/app/sql/crear_tablas_bess.sql"
readonly MIGRATION_FILE="$REPO_ROOT/production/app/sql/20260909_run_input_snapshot.sql"
readonly SEED_FILE="$REPO_ROOT/production/app/sql/20260909_seed_snapshot_test.sql"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Ejecuta este instalador con sudo." >&2
    exit 1
fi
for file in "$SCHEMA_FILE" "$MIGRATION_FILE" "$SEED_FILE"; do
    [[ -f "$file" ]] || { echo "Falta el archivo esperado: $file" >&2; exit 1; }
done
if runuser -u postgres -- psql -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$TEST_DATABASE'" | grep -qx 1; then
    echo "La base $TEST_DATABASE ya existe. No se ha modificado." >&2
    exit 1
fi

runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d postgres -c \
    "CREATE DATABASE $TEST_DATABASE WITH TEMPLATE template0 ENCODING 'UTF8'"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d postgres -c \
    "ALTER DATABASE $TEST_DATABASE SET timezone TO 'Europe/Madrid'"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$TEST_DATABASE" -f "$SCHEMA_FILE"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$TEST_DATABASE" -f "$MIGRATION_FILE"

actual_database="$(runuser -u postgres -- psql -d "$TEST_DATABASE" -Atqc 'SELECT current_database()')"
[[ "$actual_database" == "$TEST_DATABASE" ]] || { echo "Base inesperada: $actual_database" >&2; exit 1; }
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$TEST_DATABASE" -f "$SEED_FILE"

echo "Base aislada creada: $TEST_DATABASE"
echo "Contiene solo 48 precios y perfiles sintéticos; tfm_energia no se ha modificado."
