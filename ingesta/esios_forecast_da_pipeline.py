"""
TFM Energia UCM — ESIOS Forecast DA Daily Pipeline v3
Descarga automaticamente las previsiones day-ahead de ESIOS
y las carga en la tabla esios_forecast_da.

Cambios v3 (02/08/2026):
  - FIX CRITICO: añadido time_agg=average a la URL de la API.
    ESIOS agrega por SUMA por defecto con time_trunc=hour, lo que daba
    valores x4 en los indicadores nativos cuarto-horarios.
  - Añadidos indicadores 2563 (demanda de mercado sin autoconsumo),
    462 (potencia indisponible en PBF), 543 (prevision solar termica) y
    570 (capacidad prevista enlace Peninsula-Baleares). Total: 15.
  - ESPORADICOS ampliado: las NTC diarias no publican en los primeros
    años de la serie, y no deben disparar reintentos.
  - Soporte dias 23h/24h/25h (cambio de hora) via expected_hours_utc().

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
DIAS_REVISION        = 7
TIMEOUT_SEC          = 60
PAUSE_API_SEC        = 0.5
TZ_SPAIN             = ZoneInfo("Europe/Madrid")

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.esios.ree.es/indicators"

# time_agg=average es OBLIGATORIO: ESIOS agrega por SUMA por defecto cuando se
# usa time_trunc=hour sin especificar time_agg.
# Verificado 02/08/2026 con ingesta/_tests/ESIOS_TEST_10249_demanda_residual.py:
# el indicador 10249 es nativo cuarto-horario (96 valores/dia) y sin time_agg
# devolvia x4 exacto (94.283 en vez de 23.570,75 a las 00:00 del 28-jul-2026).
# Se comprobo que time_trunc=hour y time_trunc=hour&time_agg=sum dan resultados
# IDENTICOS, lo que confirma que el default de ESIOS es sum.
# AVERAGE y no SUM porque todos estos indicadores son POTENCIA (MW).
# Los programas del PBF, en cambio, son MWh y requieren SUM.
TIME_AGG = "average"

# MODO_ACTUALIZAR: si True, el pipeline no solo rellena NULLs sino que tambien
# sobrescribe los valores ya cargados cuando difieren de lo que devuelve la API.
# Necesario porque varios indicadores se REVISAN despues de la primera
# publicacion: el 10249 (demanda residual) y el 543 (solar termica) se
# actualizan cada hora, y las NTC pueden recalcularse. Con solo update_nulls()
# el primer valor cargado se quedaba fijo para siempre.
MODO_ACTUALIZAR = True
TOLERANCIA      = 0.01

INDICADORES = {
    1775:  "demanda_prev_mw",           # Prevision diaria D+1 demanda (incluye autoconsumo desde 11/12/2025)
    2563:  "demanda_mercado_prev_mw",   # Prevision diaria demanda EN EL MERCADO (sin autoconsumo)
    1777:  "gen_wind_prev_mw",          # Prevision diaria D+1 eolica
    1779:  "gen_solar_pv_prev_mw",      # Prevision diaria D+1 fotovoltaica
    10358: "gen_renovables_prev_mw",    # Prevision diaria D+1 eolica + fotovoltaica
    10249: "demanda_residual_prev_mw",  # Prevision demanda residual (OJO: se actualiza cada hora)
    1844:  "ntc_fr_imp_prev_mw",
    1848:  "ntc_fr_exp_prev_mw",
    1845:  "ntc_pt_imp_prev_mw",
    1849:  "ntc_pt_exp_prev_mw",
    1846:  "ntc_ma_imp_prev_mw",
    1850:  "ntc_ma_exp_prev_mw",
    462:   "potencia_indisp_pbf_mw",    # Potencia indisponible de generacion en PBF
    543:   "gen_solartermica_prev_mw",  # Prevision solar termica (OJO: se actualiza cada hora)
    570:   "cap_baleares_prev_mw",      # Capacidad prevista enlace Peninsula-Baleares (~10:30 D+1)
}

# Indicadores criticos — si faltan, el dia NO se considera completo
# Las cuatro series canonicas de la Circular 4/2019 de la CNMC: publicadas con
# hora fija antes de las 11:00, una hora antes del cierre del mercado diario.
# Son las unicas usables como features sin fuga de informacion, y las que
# determinan si el dia se considera completo.
KEY_COLS = ["demanda_prev_mw", "gen_wind_prev_mw",
            "gen_solar_pv_prev_mw", "gen_renovables_prev_mw"]

# Indicadores esporadicos — null es valido, no se reintenta por ellos
# 2563 y 462 son relativamente recientes: pueden no tener datos historicos
# Indicadores esporadicos: null es un resultado valido, no se reintenta por ellos.
# Verificado 02/08/2026 con el recalculo historico: las seis NTC de horizonte
# diario (1844-1850) devuelven 0 filas para enero de 2020, es decir que no
# publicaban en los primeros años de la serie. Lo mismo puede pasar con 2563,
# 462, 543 y 570. Sin estar aqui, cada una dispararia reintentos cada 5 min
# durante 5 horas buscando datos que no existen.
ESPORADICOS = {
    "demanda_mercado_prev_mw",
    "potencia_indisp_pbf_mw",
    "gen_solartermica_prev_mw",
    "cap_baleares_prev_mw",
    "ntc_fr_imp_prev_mw", "ntc_fr_exp_prev_mw",
    "ntc_pt_imp_prev_mw", "ntc_pt_exp_prev_mw",
    "ntc_ma_imp_prev_mw", "ntc_ma_exp_prev_mw",
    "demanda_residual_prev_mw",
}

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

# ── Helpers UTC / hora española ────────────────────────────────────────────────

def expected_hours_utc(target: date) -> set:
    """
    Timestamps UTC esperados para el dia target en hora española.
    Soporta 23h (cambio a verano), 24h y 25h (cambio a invierno).
    """
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    start_utc   = start_spain.astimezone(timezone.utc)
    end_utc     = end_spain.astimezone(timezone.utc)
    hours = set()
    current = start_utc
    while current <= end_utc:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def day_range_utc(target: date) -> tuple[datetime, datetime]:
    """Rango UTC [inicio, fin] que corresponde al dia target en hora española."""
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    return start_spain.astimezone(timezone.utc), end_spain.astimezone(timezone.utc)

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
    Filtra los resultados para devolver solo timestamps del dia target
    en hora española.
    """
    start_utc = target - timedelta(days=1)
    end_utc   = target + timedelta(days=1)

    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={start_utc}T00:00:00"
           f"&end_date={end_utc}T23:59:59"
           f"&time_trunc=hour"
           f"&time_agg={TIME_AGG}")

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
        except Exception:
            if intento < 3:
                time.sleep(5 * intento)
    return {}

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_day_status(conn, target: date, log) -> dict:
    """
    Comprueba cuantas horas del dia target (en hora española) tenemos en BD.
    Usa expected_hours_utc() para soportar dias de 23h/24h/25h.
    """
    expected   = expected_hours_utc(target)
    n_expected = len(expected)
    start_utc, end_utc = day_range_utc(target)

    null_check = " OR ".join([f"{c} IS NULL" for c in KEY_COLS])

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s
        """, (start_utc, end_utc))
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT COUNT(*) FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s AND NOT ({null_check})
        """, (start_utc, end_utc))
        completas = cur.fetchone()[0]

    es_completo = total >= n_expected and completas >= n_expected
    pct = total / n_expected * 100 if n_expected else 0

    log.info(f"  [{target}] {total}/{n_expected}h | {completas} completas | {pct:.0f}% "
             f"| {'✅ COMPLETO' if es_completo else f'⚠️ faltan {n_expected-total}h'}")

    return {
        "total": total,
        "completas": completas,
        "es_completo": es_completo,
        "n_expected": n_expected,
        "pct": pct,
    }


def get_existing_for_day(conn, col: str, target: date) -> tuple[set, set]:
    """Devuelve (timestamps_existentes, timestamps_con_null) para el dia target."""
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT datetime FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s
        """, (start_utc, end_utc))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"""
            SELECT datetime FROM esios_forecast_da
            WHERE datetime >= %s AND datetime <= %s AND {col} IS NULL
        """, (start_utc, end_utc))
        with_nulls = {row[0] for row in cur.fetchall()}

    return existing, with_nulls


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


def update_nulls(conn, records: list, col: str) -> int:
    """Solo rellena donde la columna es NULL. Para sobrescribir valores
    incorrectos usar el script de recalculo historico."""
    if not records:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for ts, valor in records:
            cur.execute(f"""
                UPDATE esios_forecast_da
                SET {col} = %s
                WHERE datetime = %s AND {col} IS NULL
            """, (valor, ts))
            if cur.rowcount > 0:
                updated += 1
    conn.commit()
    return updated


def upsert_overwrite(conn, records: list, col: str) -> int:
    """
    Sobrescribe si el valor de la API difiere del que hay en BD (o si es NULL).
    En lote: 1 SELECT + 1 UPDATE, para no penalizar el cron con una consulta
    por celda. Misma logica que el script de recalculo historico.
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
    expected = expected_hours_utc(target)

    for ind_id, col in INDICADORES.items():
        existing, with_nulls = get_existing_for_day(conn, col, target)

        missing    = expected - existing
        need_fetch = missing | with_nulls

        if not need_fetch and not MODO_ACTUALIZAR:
            continue

        datos = fetch_indicator_for_day(ind_id, target, headers)
        time.sleep(PAUSE_API_SEC)

        if not datos and col not in ESPORADICOS:
            log.debug(f"    {col} (id {ind_id}): sin datos de la API")

        new_records    = []
        update_records = []

        for ts, valor in datos.items():
            if ts in missing:
                new_records.append((ts, valor))
            elif MODO_ACTUALIZAR and ts in expected:
                # sobrescribe si difiere, no solo si esta NULL
                update_records.append((ts, valor))
            elif ts in with_nulls:
                update_records.append((ts, valor))

        if new_records:
            total_ins += insert_new(conn, new_records, col)

        if update_records:
            if MODO_ACTUALIZAR:
                total_upd += upsert_overwrite(conn, update_records, col)
            else:
                total_upd += update_nulls(conn, update_records, col)

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

        # Con MODO_ACTUALIZAR no se corta aqui: el dia puede estar completo pero
        # con valores revisados por ESIOS despues de la primera carga (el 10249 y
        # el 543 se actualizan cada hora). Solo se sale si el dia esta completo Y
        # no toca comprobar revisiones.
        if status["es_completo"] and not MODO_ACTUALIZAR:
            log.info(f"  ✅ Dia {target} ya completo")
            log_pipeline_db(conn, target, intento, 0, 0, "ok",
                          f"Ya completo — {status['total']}/{status['n_expected']}h",
                          time.time()-t0, log)
            conn.close()
            return True

        ins, upd = cargar_dia(target, headers, conn, log)
        status_fin  = get_day_status(conn, target, log)
        duracion    = time.time() - t0
        es_completo = status_fin["es_completo"]
        mensaje = (f"Intento {intento}: {ins} insert, {upd} update, "
                  f"{status_fin['total']}/{status_fin['n_expected']}h")

        log_pipeline_db(conn, target, intento, ins, upd,
                       "ok" if es_completo else "parcial", mensaje, duracion, log)
        conn.close()

        if es_completo:
            if ins or upd:
                log.info(f"  ✅ Dia {target} completo — {ins} nuevas, {upd} actualizadas")
            else:
                log.info(f"  ✅ Dia {target} completo, sin cambios")
            return True

        if intento >= max_intentos:
            faltan = status_fin["n_expected"] - status_fin["total"]
            log.error(f"  ❌ Max intentos — {faltan}h faltantes")
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
    log.info(f"ESIOS Forecast DA Pipeline v3 — {hoy}")
    log.info(f"Cargando previsiones para: {manana} (D+1)")
    log.info(f"Correccion UTC/hora española activa | 23/24/25h soportado")
    log.info(f"Agregacion API: time_trunc=hour & time_agg={TIME_AGG}")
    log.info(f"Modo actualizar D+1: {MODO_ACTUALIZAR} (sobrescribe si difiere)")
    log.info(f"Indicadores: {len(INDICADORES)}")
    log.info(f"Max reintentos: {MAX_HORAS_REINTENTO}h cada {PAUSA_REINTENTO_MIN}min")
    log.info("=" * 55)

    import json
    _, db_config = load_config()
    creds   = json.load(open(Path(__file__).parent / "credentials.json"))
    headers = get_headers(creds)

    log.info(f"\n=== PASO 1: Previsiones D+1 — {manana} ===")
    procesar_dia_con_reintentos(manana, headers, db_config, log)

    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    revisar_semana(headers, db_config, log)

    log.info("\nPipeline ESIOS Forecast finalizado")


if __name__ == "__main__":
    run()