"""
TFM Energia UCM — ESIOS Forecast DA Daily Pipeline v2
Descarga automaticamente las previsiones day-ahead de ESIOS
y las carga en la tabla esios_forecast_da.

Correccion UTC vs hora española:
  - España es UTC+1 en invierno (CET) y UTC+2 en verano (CEST)
  - Las previsiones se publican en hora española
  - Pedimos siempre target-1 dia hasta target+1 dia en UTC
  - Filtramos por hora española para quedarnos solo con el dia correcto

Logica:
  - Cron job a las 9:00 UTC (11:00 española)
  - Reintentos cada 5 minutos si faltan datos
  - Maximo 5 horas de reintentos (hasta las 14:00 UTC)
  - Revision ultimos 7 dias para rellenar huecos
  - Dia completo si tiene 24 horas en hora española

Cron job (servidor):
    0 9 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_forecast_da_pipeline.py >> /home/ubuntu/scripts/logs/cron_esios_forecast.log 2>&1
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

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_HORAS_REINTENTO  = 5
PAUSA_REINTENTO_MIN  = 5
HORAS_TOLERANCIA     = 0    # 0 tolerancia — queremos las 24 horas exactas
DIAS_REVISION        = 7
TIMEOUT_SEC          = 60
PAUSE_API_SEC        = 0.5
TZ_SPAIN             = ZoneInfo("Europe/Madrid")

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.esios.ree.es/indicators"

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

KEY_COLS = ["demanda_prev_mw", "gen_wind_prev_mw", "gen_solar_pv_prev_mw"]

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"esios_forecast_pipeline_{target_date}.log"
    logger = logging.getLogger(f"esios_forecast_{target_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# ── ESIOS API ──────────────────────────────────────────────────────────────────

def get_headers(creds: dict) -> dict:
    return {
        "Host": creds["Host"],
        "x-api-key": creds["x-api-key"],
        "Accept": "application/json"
    }


def fetch_indicator_for_day(ind_id: int, target: date, headers: dict) -> dict:
    """
    Descarga datos para un dia en hora española.
    Pide target-1 hasta target+1 en UTC para cubrir el desfase horario
    en cualquier epoca del año (CET UTC+1 o CEST UTC+2).
    Filtra los resultados para devolver solo timestamps del dia target en hora española.
    """
    # Pedir con margen de +-1 dia para cubrir desfase UTC/hora española
    start_utc = target - timedelta(days=1)
    end_utc   = target + timedelta(days=1)

    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={start_utc}T00:00:00"
           f"&end_date={end_utc}T23:59:59"
           f"&time_trunc=hour")

    for intento in range(1, 4):
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
                        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if dt_utc.tzinfo is None:
                            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

                        # Filtrar solo timestamps que en hora española son del dia target
                        dt_spain = dt_utc.astimezone(TZ_SPAIN)
                        if dt_spain.date() == target:
                            result[dt_utc] = float(val)
                    except Exception:
                        pass
            return result
        except Exception as e:
            if intento < 3:
                time.sleep(5 * intento)
    return {}

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_day_status(conn, target: date, log) -> dict:
    """
    Comprueba cuantas horas del dia target (en hora española) tenemos en BD.
    La tabla guarda timestamps en UTC — convertimos a hora española para contar.
    """
    null_check = " OR ".join([f"{c} IS NULL" for c in KEY_COLS])

    # Calcular rango UTC que corresponde al dia target en hora española
    # Inicio: medianoche hora española del dia target → UTC
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0,
                          tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0,
                          tzinfo=TZ_SPAIN)
    start_utc = start_spain.astimezone(timezone.utc)
    end_utc   = end_spain.astimezone(timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM esios_forecast_da
            WHERE time >= %s AND time <= %s
        """, (start_utc, end_utc))
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT COUNT(*) FROM esios_forecast_da
            WHERE time >= %s AND time <= %s AND NOT ({null_check})
        """, (start_utc, end_utc))
        completas = cur.fetchone()[0]

    es_completo = total >= 24 and completas >= 24
    pct = total / 24 * 100

    log.info(f"  [{target}] {total}/24h | {completas} completas | {pct:.0f}% "
             f"| {'✅ COMPLETO' if es_completo else f'⚠️ faltan {24-total}h'}")

    return {
        "total": total,
        "completas": completas,
        "es_completo": es_completo,
        "pct": pct,
    }


def get_existing_for_day(conn, col: str, target: date) -> tuple[set, set]:
    """Devuelve (timestamps_existentes, timestamps_con_null) para el dia target en hora española."""
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    start_utc = start_spain.astimezone(timezone.utc)
    end_utc   = end_spain.astimezone(timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT time FROM esios_forecast_da
            WHERE time >= %s AND time <= %s
        """, (start_utc, end_utc))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"""
            SELECT time FROM esios_forecast_da
            WHERE time >= %s AND time <= %s AND {col} IS NULL
        """, (start_utc, end_utc))
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


def log_pipeline_db(conn, target, intento, ins, upd, status, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (f"esios_forecast_da_{target}_intento_{intento}", target, target,
                  ins + upd, status, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()

# ── Cargar un dia ──────────────────────────────────────────────────────────────

def cargar_dia(target: date, headers: dict, conn, log) -> tuple[int, int]:
    total_ins = total_upd = 0

    for ind_id, col in INDICADORES.items():
        existing, with_nulls = get_existing_for_day(conn, col, target)

        # Calcular horas esperadas del dia target en hora española → UTC
        expected = set()
        for h in range(24):
            dt_spain = datetime(target.year, target.month, target.day, h, 0, 0,
                               tzinfo=TZ_SPAIN)
            expected.add(dt_spain.astimezone(timezone.utc))

        missing    = expected - existing
        need_fetch = missing | with_nulls

        if not need_fetch:
            continue

        # Pedir con margen +-1 dia y filtrar por hora española
        datos = fetch_indicator_for_day(ind_id, target, headers)
        time.sleep(PAUSE_API_SEC)

        new_records    = []
        update_records = []

        for ts, valor in datos.items():
            if ts in missing:
                new_records.append((ts, valor))
            elif ts in with_nulls:
                update_records.append((ts, valor))

        if new_records:
            n = insert_new(conn, new_records, col)
            total_ins += n

        if update_records:
            n = update_nulls(conn, update_records, col)
            total_upd += n

    return total_ins, total_upd

# ── Procesar dia con reintentos ────────────────────────────────────────────────

def procesar_dia_con_reintentos(target: date, headers: dict, db_config: dict, log) -> bool:
    max_intentos = (MAX_HORAS_REINTENTO * 60) // PAUSA_REINTENTO_MIN
    intento = 1

    while intento <= max_intentos:
        t0 = time.time()
        log.info(f"  Intento {intento}/{max_intentos} — {datetime.now().strftime('%H:%M:%S')}")

        try:
            conn = psycopg2.connect(**db_config)
        except Exception as e:
            log.error(f"  Error BD: {e}")
            time.sleep(PAUSA_REINTENTO_MIN * 60)
            intento += 1
            continue

        status = get_day_status(conn, target, log)

        if status["es_completo"]:
            log.info(f"  ✅ Dia {target} ya completo")
            log_pipeline_db(conn, target, intento, 0, 0, "ok",
                          f"Ya completo — {status['total']}/24h", time.time()-t0, log)
            conn.close()
            return True

        ins, upd = cargar_dia(target, headers, conn, log)
        status_fin = get_day_status(conn, target, log)
        duracion   = time.time() - t0
        es_completo = status_fin["es_completo"]
        mensaje = (f"Intento {intento}: {ins} insert, {upd} update, "
                  f"{status_fin['total']}/24h")

        log_pipeline_db(conn, target, intento, ins, upd,
                       "ok" if es_completo else "parcial", mensaje, duracion, log)
        conn.close()

        if es_completo:
            log.info(f"  ✅ Dia {target} completo tras intento {intento}")
            return True

        if intento >= max_intentos:
            log.error(f"  ❌ Max intentos — {24 - status_fin['total']}h faltantes")
            return False

        log.info(f"  Esperando {PAUSA_REINTENTO_MIN} min...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    return False


def revisar_semana(headers: dict, db_config: dict, log):
    hoy = date.today()
    log.info(f"\n--- Revision ultimos {DIAS_REVISION} dias ---")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD: {e}")
        return

    for i in range(1, DIAS_REVISION + 1):
        dia = hoy - timedelta(days=i)
        status = get_day_status(conn, dia, log)
        if status["es_completo"]:
            continue
        log.info(f"  Rellenando {dia}...")
        ins, upd = cargar_dia(dia, headers, conn, log)
        log.info(f"  {dia}: {ins} insert, {upd} update")

    conn.close()
    log.info("--- Revision semanal completada ---\n")

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    hoy    = date.today()
    manana = hoy + timedelta(days=1)
    log    = setup_logger(hoy)

    log.info("=" * 55)
    log.info(f"ESIOS Forecast DA Pipeline v2 — {hoy}")
    log.info(f"Cargando previsiones para: {manana} (D+1)")
    log.info(f"Correccion UTC/hora española activa (ZoneInfo Europe/Madrid)")
    log.info(f"Max reintentos: {MAX_HORAS_REINTENTO}h cada {PAUSA_REINTENTO_MIN}min")
    log.info("=" * 55)

    import json
    _, db_config = load_config()
    creds   = json.load(open(Path(__file__).parent / "credentials.json"))
    headers = get_headers(creds)

    # PASO 1 — Previsiones de mañana con reintentos
    log.info(f"\n=== PASO 1: Previsiones D+1 — {manana} ===")
    procesar_dia_con_reintentos(manana, headers, db_config, log)

    # PASO 2 — Revision ultimos 7 dias
    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    revisar_semana(headers, db_config, log)

    log.info("\nPipeline ESIOS Forecast finalizado")


if __name__ == "__main__":
    run()
