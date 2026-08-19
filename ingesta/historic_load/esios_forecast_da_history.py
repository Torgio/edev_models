"""
TFM Energia UCM — ESIOS Forecast DA Loader v3
Descarga previsiones day-ahead de ESIOS → esios_forecast_da.

Mejoras v3 (02/08/2026):
  - MODO_RECALCULO: descarga siempre y sobrescribe si el valor difiere,
    en vez de rellenar solo NULLs. Necesario para corregir el bug x4.
  - FIX desfase UTC / hora española: el rango de 'expected' y de
    get_chunk_status() se amplia +-1 dia. Las 00:00 y 01:00 hora española
    son las 22:00 y 23:00 UTC del dia anterior (verano, UTC+2), y sin el
    margen esas dos horas quedaban fuera del set y no se corregian
    (se veia como "166 upd de 168 API rows").
  - upsert_overwrite() optimizado: 1 SELECT + 1 UPDATE por chunk en lote,
    en vez de 2 consultas por celda. Reduce el recalculo 2020-2026 de
    ~30 horas a ~40 minutos.
  - Añadidos indicadores 2563 (demanda de mercado sin autoconsumo) y
    462 (potencia indisponible en PBF).

Usage:
    python esios_forecast_da_history.py
"""

import logging
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))

from config import load_config


# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI              ║
# ╚══════════════════════════════════════════════════════════════╝

MODO_RECALCULO = True       # True  = descarga siempre y sobrescribe si difiere
                            # False = solo rellena huecos y nulls (mas rapido)

MODE       = "range"         # "test" | "range" | "yesterday"
START_DATE = "2026-08-02"   # Inicio
END_DATE   = "2026-08-05"   # Fin (ignorado en MODE=test: usa START_DATE + 6 dias)

# ╚══════════════════════════════════════════════════════════════╝

# time_agg=average es OBLIGATORIO: ESIOS agrega por SUMA por defecto cuando se
# usa time_trunc=hour sin especificar time_agg.
# Verificado 02/08/2026 con ingesta/_tests/ESIOS_TEST_10249_demanda_residual.py:
# el indicador 10249 es nativo cuarto-horario (96 valores/dia) y sin time_agg
# devolvia x4 exacto (94.283 en vez de 23.570,75 a las 00:00 del 28-jul-2026).
# Se comprobo que time_trunc=hour y time_trunc=hour&time_agg=sum dan resultados
# IDENTICOS, lo que confirma que el default de ESIOS es sum.
# AVERAGE y no SUM porque todos estos indicadores son POTENCIA (MW).
TIME_AGG = "average"

# Afectados por el bug x4 (nativos cuarto-horarios): 10249 y las seis NTC.
# Correctos desde el inicio (horarios nativos): 1775, 1777, 1779, 10358.
INDICADORES = {
    1775:  "demanda_prev_mw",
    2563:  "demanda_mercado_prev_mw",
    1777:  "gen_wind_prev_mw",
    1779:  "gen_solar_pv_prev_mw",
    10358: "gen_renovables_prev_mw",
    10249: "demanda_residual_prev_mw",
    1844:  "ntc_fr_imp_prev_mw",
    1848:  "ntc_fr_exp_prev_mw",
    1845:  "ntc_pt_imp_prev_mw",
    1849:  "ntc_pt_exp_prev_mw",
    1846:  "ntc_ma_imp_prev_mw",
    1850:  "ntc_ma_exp_prev_mw",
    543:   "gen_solartermica_prev_mw",
    570:   "cap_baleares_prev_mw",
}

BASE_URL    = "https://api.esios.ree.es/indicators"
CHUNK_DAYS  = 7
PAUSE_SEC   = 1.0
TIMEOUT_SEC = 60
MAX_RETRIES = 3
TOLERANCIA  = 0.01

# Margen de dias a ambos lados del chunk para cubrir el desfase UTC/CEST.
# El solapamiento entre chunks es inocuo: upsert_overwrite() solo escribe
# si el valor difiere de lo que ya hay en BD.
MARGEN_DIAS = 1

TZ_SPAIN = ZoneInfo("Europe/Madrid")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("esios_forecast_da")

# ── Fecha resolution ───────────────────────────────────────────────────────────

def resolve_dates() -> tuple[date, date]:
    if MODE == "yesterday":
        d = date.today() - timedelta(days=1)
        return d, d
    elif MODE == "test":
        start = date.fromisoformat(START_DATE)
        return start, start + timedelta(days=6)
    elif MODE == "range":
        return date.fromisoformat(START_DATE), date.fromisoformat(END_DATE)
    else:
        raise ValueError(f"Unknown MODE: {MODE}")


def chunk_bounds_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """
    Rango UTC ampliado +-MARGEN_DIAS para un chunk.
    Necesario porque la tabla guarda timestamps UTC que corresponden a
    dias españoles: las 00:00 hora española del dia D son las 22:00 UTC
    del dia D-1 en verano (CEST, UTC+2).
    """
    start_dt = (datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
                - timedelta(days=MARGEN_DIAS))
    end_dt   = (datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc)
                + timedelta(days=MARGEN_DIAS))
    return start_dt, end_dt

# ── ESIOS API ──────────────────────────────────────────────────────────────────

def get_headers(creds: dict) -> dict:
    return {
        "Host": creds["Host"],
        "x-api-key": creds["x-api-key"],
        "Accept": "application/json"
    }


def fetch_chunk(ind_id: int, start: date, end: date, headers: dict) -> dict:
    """Descarga un chunk de datos de ESIOS con reintentos."""
    # Se pide con el mismo margen para que la API devuelva tambien las horas
    # frontera que en hora española pertenecen al chunk.
    api_start = start - timedelta(days=MARGEN_DIAS)
    api_end   = end + timedelta(days=MARGEN_DIAS)

    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={api_start}T00:00:00"
           f"&end_date={api_end}T23:59:59"
           f"&time_trunc=hour"
           f"&time_agg={TIME_AGG}")

    for intento in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            r.raise_for_status()
            valores = r.json().get("indicator", {}).get("values", [])
            result = {}
            for v in valores:
                dt_str = v.get("datetime_utc") or v.get("datetime")
                val    = v.get("value")
                if dt_str and val is not None:
                    try:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        result[dt] = float(val)
                    except Exception:
                        pass
            return result
        except Exception as e:
            log.warning(f"    Intento {intento}/{MAX_RETRIES} fallido: {e}")
            if intento < MAX_RETRIES:
                time.sleep(5 * intento)
    return {}

# ── BD helpers por chunk ───────────────────────────────────────────────────────

def get_chunk_status(conn, col: str, start: date, end: date) -> tuple[set, set]:
    """Devuelve (horas_existentes, horas_con_null_en_col) para un chunk."""
    start_dt, end_dt = chunk_bounds_utc(start, end)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT datetime FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s
        """, (start_dt, end_dt))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"""
            SELECT datetime FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s AND {col} IS NULL
        """, (start_dt, end_dt))
        with_nulls = {row[0] for row in cur.fetchall()}

    return existing, with_nulls


def expected_hours(start: date, end: date) -> set:
    """Set de timestamps UTC horarios del chunk, con margen +-MARGEN_DIAS."""
    start_dt, end_dt = chunk_bounds_utc(start, end)
    horas = set()
    d = start_dt
    while d <= end_dt:
        horas.add(d)
        d += timedelta(hours=1)
    return horas


def target_hours_utc(start: date, end: date) -> set:
    """
    Timestamps UTC que corresponden EXACTAMENTE a los dias españoles del chunk,
    sin margen. Se usa para decidir que filas se pueden CREAR.

    El margen de expected_hours() es necesario para poder ACTUALIZAR las horas
    frontera (las 00:00 hora española son las 22:00/23:00 UTC del dia anterior),
    pero no debe usarse para insertar: si no, el primer chunk crea filas del dia
    anterior al periodo pedido, con una sola columna rellena y el resto NULL.
    Soporta dias de 23h/24h/25h por cambio de hora.
    """
    horas = set()
    d = start
    while d <= end:
        h0 = datetime(d.year, d.month, d.day, 0, 0, tzinfo=TZ_SPAIN).astimezone(timezone.utc)
        h23 = datetime(d.year, d.month, d.day, 23, 0, tzinfo=TZ_SPAIN).astimezone(timezone.utc)
        t = h0
        while t <= h23:
            horas.add(t)
            t += timedelta(hours=1)
        d += timedelta(days=1)
    return horas


def insert_new(conn, records: list, col: str) -> int:
    if not records:
        return 0
    sql = f"""
        INSERT INTO esios_forecast_da (datetime, {col})
        VALUES %s
        ON CONFLICT (time) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=500)
    conn.commit()
    return len(records)


def upsert_overwrite(conn, records: list, col: str) -> int:
    """
    Sobrescribe si el valor difiere del que hay en BD (o si es NULL).
    Optimizado: 1 SELECT + 1 UPDATE en lote por chunk, en vez de 2
    consultas por celda. Con ~70ms de latencia al servidor, la version
    anterior tardaba ~25s por indicador y chunk.
    """
    if not records:
        return 0

    ts_list = [ts for ts, _ in records]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT datetime, {col} FROM esios_forecast_da WHERE datetime = ANY(%s)",
            (ts_list,)
        )
        actuales = dict(cur.fetchall())

        cambios = []
        for ts, valor in records:
            if ts not in actuales:
                continue
            act = actuales[ts]
            if act is None or abs(float(act) - valor) > TOLERANCIA:
                cambios.append((ts, valor))

        if not cambios:
            return 0

        execute_values(cur, f"""
            UPDATE esios_forecast_da AS t
            SET {col} = v.valor
            FROM (VALUES %s) AS v(ts, valor)
            WHERE t.time = v.ts
        """, cambios, template="(%s, %s::numeric)", page_size=500)

    conn.commit()
    return len(cambios)

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    import json
    start_date, end_date = resolve_dates()

    _, db_config = load_config()
    creds   = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    headers = get_headers(creds)
    conn    = psycopg2.connect(**db_config)

    log.info("=" * 62)
    log.info(f"  MODE        : {MODE}")
    log.info(f"  Period      : {start_date} → {end_date}")
    log.info(f"  Chunks      : {CHUNK_DAYS} dias (margen +-{MARGEN_DIAS}d)")
    log.info(f"  Recalculo   : {MODO_RECALCULO}")
    log.info(f"  Agregacion  : time_trunc=hour & time_agg={TIME_AGG}")
    log.info(f"  Indicadores : {len(INDICADORES)}")
    log.info("=" * 62)

    total_ins = total_upd = total_skip = 0
    resumen = {}

    for ind_id, col in INDICADORES.items():
        log.info(f"\n── ID {ind_id} → '{col}' ──")
        ind_ins = ind_upd = ind_skip = 0
        current = start_date
        chunk_n = 0

        while current <= end_date:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end_date)
            chunk_n  += 1

            existing, with_nulls = get_chunk_status(conn, col, current, chunk_end)
            expected = expected_hours(current, chunk_end)      # con margen: para UPDATE
            target   = target_hours_utc(current, chunk_end)     # sin margen: para INSERT

            missing    = target - existing
            need_fetch = missing | with_nulls

            if not need_fetch and not MODO_RECALCULO:
                ind_skip += len(existing)
                current = chunk_end + timedelta(days=1)
                continue

            datos = fetch_chunk(ind_id, current, chunk_end, headers)
            time.sleep(PAUSE_SEC)

            new_records    = []
            update_records = []

            for ts, valor in datos.items():
                if ts in missing:
                    new_records.append((ts, valor))
                elif ts in expected:
                    update_records.append((ts, valor))

            n_ins = insert_new(conn, new_records, col) if new_records else 0
            n_upd = upsert_overwrite(conn, update_records, col) if update_records else 0
            ind_ins += n_ins
            ind_upd += n_upd

            log.info(f"  [{chunk_n}] {current}→{chunk_end}: "
                     f"{n_ins} ins / {n_upd} corregidas / "
                     f"{len(datos)} API rows")

            current = chunk_end + timedelta(days=1)

        log.info(f"  TOTAL ID {ind_id}: INSERT={ind_ins} CORREGIDAS={ind_upd} SKIP={ind_skip}")
        resumen[col] = (ind_ins, ind_upd)
        total_ins  += ind_ins
        total_upd  += ind_upd
        total_skip += ind_skip

    conn.close()

    log.info("\n" + "=" * 62)
    log.info("  RESUMEN POR COLUMNA")
    log.info("=" * 62)
    for col, (ins, upd) in sorted(resumen.items(), key=lambda x: -x[1][1]):
        if ins or upd:
            log.info(f"  {col:28} ins={ins:>7}  corregidas={upd:>7}")
    log.info("=" * 62)
    log.info(f"  DONE: {total_ins} inserted | {total_upd} corregidas | {total_skip} skipped")
    log.info("=" * 62)


if __name__ == "__main__":
    run()
