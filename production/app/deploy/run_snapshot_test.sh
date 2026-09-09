#!/usr/bin/env bash
set -Eeuo pipefail

readonly TEST_DATABASE="tfm_energia_test"
readonly REPO_ROOT="${1:-/home/ubuntu/scripts}"
readonly PYTHON_BIN="${2:-/home/ubuntu/tfm-env/bin/python}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Ejecuta esta prueba con sudo." >&2
    exit 1
fi
[[ -x "$PYTHON_BIN" ]] || { echo "Python no disponible: $PYTHON_BIN" >&2; exit 1; }
[[ -f "$REPO_ROOT/production/app/caso.py" ]] || { echo "Código no disponible en $REPO_ROOT" >&2; exit 1; }
actual_database="$(runuser -u postgres -- psql -d "$TEST_DATABASE" -Atqc 'SELECT current_database()')"
[[ "$actual_database" == "$TEST_DATABASE" ]] || { echo "Base inesperada: $actual_database" >&2; exit 1; }

cd "$REPO_ROOT"
TFM_TEST_DB_NAME="$TEST_DATABASE" TFM_EMAIL="snapshot-test@local.invalid" \
    "$PYTHON_BIN" production/app/caso.py ejecutar --code SNAPSHOT-TEST \
    --escenarios 1 --sin-despacho

runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$TEST_DATABASE" <<'SQL'
DO $$
DECLARE rid integer; snap jsonb;
BEGIN
    SELECT run_id, input_snapshot INTO rid, snap
    FROM public.app_case_run ORDER BY run_id DESC LIMIT 1;
    IF rid IS NULL OR snap IS NULL THEN RAISE EXCEPTION 'No snapshot was stored'; END IF;
    IF snap #>> '{period,date_from}' <> '2026-01-05'
       OR snap #>> '{period,date_to}' <> '2026-01-06'
       OR (snap #>> '{battery,power_mw}')::numeric <> .05
       OR (snap #>> '{battery,capacity_mwh}')::numeric <> .20
       OR (snap #>> '{consumption,annual_mwh}')::numeric <> 350
       OR (snap #>> '{generation,capacity_mwp}')::numeric <> .25 THEN
        RAISE EXCEPTION 'Stored snapshot does not match the synthetic inputs';
    END IF;
    BEGIN
        UPDATE public.app_case_run SET input_snapshot = '{}'::jsonb WHERE run_id = rid;
        RAISE EXCEPTION 'Immutability trigger did not reject the update';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT LIKE 'Study inputs cannot be changed%' THEN RAISE; END IF;
    END;
END;
$$;
SELECT run_id, case_id, input_snapshot #>> '{case,name}' AS caso,
       input_snapshot #>> '{battery,power_mw}' AS potencia_mw,
       input_snapshot #>> '{consumption,annual_mwh}' AS consumo_mwh_anual,
       input_snapshot #>> '{generation,capacity_mwp}' AS solar_mwp
FROM public.app_case_run ORDER BY run_id DESC LIMIT 1;
SQL

echo "Prueba superada: ejecución, copia de parámetros e inmutabilidad verificadas."
