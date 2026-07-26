"""
TFM Energia UCM — ESIOS Forecast DA Loader v2
Descarga previsiones day-ahead de ESIOS → esios_forecast_da.

Mejoras v2:
  - Consulta BD por chunks (no toda la tabla de golpe)
  - CHUNK_DAYS = 7 dias
  - Reintento automatico por chunk si falla
  - Timeout agresivo en API

Usage:
    python esios_forecast_da_load.py
"""

import logging
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import requests
import psycopg2
from psycopg2.extras import execute_values

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from config import load_config


# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI             ║
# ╚══════════════════════════════════════════════════════════════╝

MODE       = "range"        # "test" | "range" | "yesterday"
START_DATE = "2020-01-01"   # Inicio
END_DATE   = "2026-06-25"   # Fin

# ╚══════════════════════════════════════════════════════════════╝

INDICADORES = {
    1775:  "demanda_prev_mw",
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
}

BASE_URL    = "https://api.esios.ree.es/indicators"
CHUNK_DAYS  = 7
PAUSE_SEC   = 1.0
TIMEOUT_SEC = 60
MAX_RETRIES = 3

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

# ── ESIOS API ──────────────────────────────────────────────────────────────────

def get_headers(creds: dict) -> dict:
    return {
        "Host": creds["Host"],
        "x-api-key": creds["x-api-key"],
        "Accept": "application/json"
    }


def fetch_chunk(ind_id: int, start: date, end: date, headers: dict) -> dict:
    """Descarga un chunk de datos de ESIOS con reintentos."""
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={start}T00:00:00"
           f"&end_date={end}T23:59:59"
           f"&time_trunc=hour")

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
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt   = datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT time FROM esios_forecast_da
            WHERE time >= %s AND time <= %s
        """, (start_dt, end_dt))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"""
            SELECT time FROM esios_forecast_da
            WHERE time >= %s AND time <= %s AND {col} IS NULL
        """, (start_dt, end_dt))
        with_nulls = {row[0] for row in cur.fetchall()}

    return existing, with_nulls


def insert_new(conn, records: list, col: str) -> int:
    if not records:
        return 0
    sql = f"""
        INSERT INTO esios_forecast_da (time, {col})
        VALUES %s
        ON CONFLICT (time) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=500)
    conn.commit()
    return len(records)


def update_nulls(conn, records: list, col: str) -> int:
    if not records:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for ts, valor in records:
            cur.execute(f"""
                UPDATE esios_forecast_da
                SET {col} = %s
                WHERE time = %s AND {col} IS NULL
            """, (valor, ts))
            if cur.rowcount > 0:
                updated += 1
    conn.commit()
    return updated

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    import json
    start_date, end_date = resolve_dates()

    _, db_config = load_config()
    creds   = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    headers = get_headers(creds)
    conn    = psycopg2.connect(**db_config)

    log.info("=" * 60)
    log.info(f"  MODE   : {MODE}")
    log.info(f"  Period : {start_date} → {end_date}")
    log.info(f"  Chunks : {CHUNK_DAYS} dias")
    log.info("=" * 60)

    total_ins = total_upd = total_skip = 0

    for ind_id, col in INDICADORES.items():
        log.info(f"\n── ID {ind_id} → '{col}' ──")
        ind_ins = ind_upd = ind_skip = 0
        current = start_date
        chunk_n = 0

        while current <= end_date:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end_date)
            chunk_n  += 1

            # Consultar BD para este chunk
            existing, with_nulls = get_chunk_status(conn, col, current, chunk_end)

            # Calcular horas esperadas en este chunk
            expected = set()
            d = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
            d_end = datetime(chunk_end.year, chunk_end.month, chunk_end.day, 23, tzinfo=timezone.utc)
            while d <= d_end:
                expected.add(d)
                d += timedelta(hours=1)

            missing    = expected - existing
            need_fetch = missing | with_nulls

            if not need_fetch:
                ind_skip += len(existing)
                current = chunk_end + timedelta(days=1)
                continue

            # Descargar chunk
            datos = fetch_chunk(ind_id, current, chunk_end, headers)
            time.sleep(PAUSE_SEC)

            new_records    = []
            update_records = []

            for ts, valor in datos.items():
                if ts in missing:
                    new_records.append((ts, valor))
                elif ts in with_nulls:
                    update_records.append((ts, valor))
                else:
                    ind_skip += 1

            if new_records:
                n = insert_new(conn, new_records, col)
                ind_ins += n

            if update_records:
                n = update_nulls(conn, update_records, col)
                ind_upd += n

            log.info(f"  [{chunk_n}] {current}→{chunk_end}: "
                     f"+{len(new_records)} ins / +{len(update_records)} upd / "
                     f"{len(datos)} API rows")

            current = chunk_end + timedelta(days=1)

        log.info(f"  TOTAL ID {ind_id}: INSERT={ind_ins} UPDATE={ind_upd} SKIP={ind_skip}")
        total_ins  += ind_ins
        total_upd  += ind_upd
        total_skip += ind_skip

    conn.close()
    log.info("\n" + "=" * 60)
    log.info(f"  DONE: {total_ins} inserted | {total_upd} updated | {total_skip} skipped")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
