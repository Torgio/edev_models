"""
TFM Energia UCM — Pipeline de actualizacion diaria automatica
Descarga datos ESIOS del dia anterior y los carga en PostgreSQL.

Logica de reintentos:
  - Primer intento a las 21:00 (via cron job)
  - Si falla, reintenta cada 10 minutos
  - Maximo 12 horas de reintentos (hasta las 09:00 del dia siguiente)
  - Para cuando todos los indicadores se cargan correctamente

Cron job (en el servidor):
    0 21 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/pipeline_diario.py >> /home/ubuntu/scripts/logs/cron.log 2>&1

Log detallado en:
    /home/ubuntu/scripts/logs/pipeline_YYYY-MM-DD.log

Usage:
    python pipeline_diario.py              # carga datos de ayer
    python pipeline_diario.py --fecha 2026-06-22   # carga fecha concreta
    python pipeline_diario.py --dias 3     # carga ultimos 3 dias
"""

import time
import logging
import argparse
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_HORAS_REINTENTO = 12        # maximo de horas intentando
PAUSA_REINTENTO_MIN = 10        # minutos entre reintentos
PAUSA_INDICADOR_SEC = 0.5       # pausa entre peticiones ESIOS

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Indicadores ESIOS — todos verificados el 23/06/2026
INDICATORS = {
    "price_eur_mwh":         (600,   None),
    "demanda_real_mw":       (1293,  8741),
    "demanda_prev_mw":       (544,   8741),
    "gen_solar_mw":          (1295,  8741),
    "gen_solar_term_real_mw":(1294,  8741),
    "gen_wind_mw":           (551,   8741),
    "gen_hidro_real_mw":     (546,   8741),
    "gen_nuclear_real_mw":   (549,   8741),
    "gen_ciclocomb_real_mw": (550,   8741),
    "gen_coal_real_mw":      (547,   8741),
    "gen_cogen_real_mw":     (553,   8741),
    "resto_gen_real_mw":     (1297,  8741),
    "saldo_francia_mw":      (10045, None),
    "saldo_portugal_mw":     (557,   8741),
    "saldo_portugal_exp_mw": (561,   8741),
    "saldo_marruecos_mw":    (10046, None),
    "gen_solar_prev_mw":     (542,   8741),
    "gen_solar_term_prev_mw":(543,   8741),
    "precio_banda_sec_mwh":  (634,   8741),
    "gen_bombeo_turb_mw":    (1152,  None),
    "cons_bombeo_mw":        (1172,  None),
    "ntc_francia_imp_mw":    (488,   8741),
    "ntc_francia_exp_mw":    (492,   8741),
    "ntc_portugal_imp_mw":   (489,   8741),
    "ntc_portugal_exp_mw":   (493,   8741),
    "ntc_marruecos_imp_mw":  (490,   8741),
    "ntc_marruecos_exp_mw":  (494,   8741),
    "co2_real_t":            (10355, 8741),
    "gen_libre_co2_mw":      (10006, 8741),
    "pct_gen_libre_co2":     (10033, 8741),
}

ESIOS_BASE = "https://api.esios.ree.es"
ALL_COLS   = ["time_qh"] + list(INDICATORS.keys())

# Columnas clave que deben tener datos para considerar el dia completo
KEY_COLS = [
    "price_eur_mwh", "demanda_real_mw", "gen_solar_mw",
    "gen_wind_mw", "gen_nuclear_real_mw", "saldo_francia_mw",
    "pct_gen_libre_co2", "ntc_francia_imp_mw",
]

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    """Configura logger con salida a fichero y consola."""
    log_file = LOGS_DIR / f"pipeline_{target_date}.log"

    logger = logging.getLogger(f"pipeline_{target_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []

    # Formato
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler fichero
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Handler consola
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_day_status(conn, target: date, log) -> dict:
    """
    Analiza el estado del dia en BD:
    - horas existentes
    - horas con nulls en columnas clave
    - % completitud
    """
    with conn.cursor() as cur:
        # Total horas del dia
        cur.execute("""
            SELECT COUNT(*) FROM marketdata_qh
            WHERE time_qh::date = %s
        """, (target,))
        total = cur.fetchone()[0]

        # Horas con nulls en columnas clave
        null_check = " OR ".join([f"{c} IS NULL" for c in KEY_COLS])
        cur.execute(f"""
            SELECT COUNT(*) FROM marketdata_qh
            WHERE time_qh::date = %s AND ({null_check})
        """, (target,))
        with_nulls = cur.fetchone()[0]

        # Detalle de nulls por columna
        null_detail = {}
        for col in KEY_COLS:
            cur.execute(f"""
                SELECT COUNT(*) FROM marketdata_qh
                WHERE time_qh::date = %s AND {col} IS NULL
            """, (target,))
            null_detail[col] = cur.fetchone()[0]

    completitud = ((total - with_nulls) / max(total, 24)) * 100 if total > 0 else 0

    status = {
        "total_horas":  total,
        "con_nulls":    with_nulls,
        "completas":    total - with_nulls,
        "completitud":  completitud,
        "null_detail":  null_detail,
        "es_completo":  total >= 23 and with_nulls == 0,
    }

    log.info(f"  BD status para {target}:")
    log.info(f"    Horas en BD    : {total}")
    log.info(f"    Horas completas: {total - with_nulls}")
    log.info(f"    Horas con nulls: {with_nulls}")
    log.info(f"    Completitud    : {completitud:.1f}%")
    for col, nulls in null_detail.items():
        if nulls > 0:
            log.warning(f"    [WARN] {col}: {nulls} nulls")

    return status


# ── API ESIOS ──────────────────────────────────────────────────────────────────

def fetch_indicator(headers, indicator_id, geo_id, target: date, log) -> pd.Series | None:
    url    = f"{ESIOS_BASE}/indicators/{indicator_id}"
    params = {
        "start_date": f"{target}T00:00:00",
        "end_date":   f"{target}T23:59:59",
        "time_trunc": "hour",
    }
    if geo_id is not None:
        params["geo_ids[]"] = geo_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            log.debug(f"    Indicador {indicator_id}: sin datos")
            return None
        df = pd.DataFrame(values)[["datetime_utc", "value"]]
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
        df = df.set_index("datetime_utc")["value"]
        return df[~df.index.duplicated(keep="first")]
    except Exception as e:
        log.error(f"    Error indicador {indicator_id}: {e}")
        return None


def fetch_day(headers, target: date, log) -> pd.DataFrame | None:
    log.info(f"  Descargando {len(INDICATORS)} indicadores ESIOS para {target}...")
    frames = {}
    ok, fail = 0, 0
    for col, (ind_id, geo_id) in INDICATORS.items():
        serie = fetch_indicator(headers, ind_id, geo_id, target, log)
        if serie is not None:
            frames[col] = serie
            ok += 1
        else:
            fail += 1
        time.sleep(PAUSA_INDICADOR_SEC)

    log.info(f"  Indicadores OK: {ok} | Fallidos: {fail}")
    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()


# ── INSERT + UPDATE ────────────────────────────────────────────────────────────

def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    """INSERT filas nuevas + UPDATE columnas null en filas existentes."""
    data_cols = list(INDICATORS.keys())
    ins, upd  = 0, 0

    # Horas ya existentes en BD para este dia
    with conn.cursor() as cur:
        cur.execute("""
            SELECT time_qh FROM marketdata_qh
            WHERE time_qh::date = %s
        """, (target,))
        existing = {row[0] for row in cur.fetchall()}

    # INSERT — horas nuevas
    df_new = df[~df["time_qh"].isin(existing)]
    if not df_new.empty:
        cols = [c for c in ALL_COLS if c in df_new.columns]
        records = [
            tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
            for _, row in df_new.iterrows()
        ]
        sql = f"""
            INSERT INTO marketdata_qh ({', '.join(cols)})
            VALUES %s
            ON CONFLICT (time_qh) DO NOTHING
        """
        with conn.cursor() as cur:
            execute_values(cur, sql, records, page_size=500)
        conn.commit()
        ins = len(records)
        log.info(f"  INSERT: {ins} filas nuevas")

    # UPDATE — columnas null en filas existentes
    df_exist = df[df["time_qh"].isin(existing)]
    if not df_exist.empty:
        cols_str = ", ".join([c for c in data_cols if c in df_exist.columns])
        with conn.cursor() as cur:
            for _, row in df_exist.iterrows():
                ts = row["time_qh"]
                cur.execute(f"SELECT {cols_str} FROM marketdata_qh WHERE time_qh = %s", (ts,))
                db_row = cur.fetchone()
                if not db_row:
                    continue
                cols_list = [c for c in data_cols if c in row.index]
                to_update = {}
                for i, col in enumerate(cols_list):
                    if db_row[i] is None and not pd.isna(row.get(col)):
                        to_update[col] = row[col]
                if to_update:
                    set_clause = ", ".join([f"{c} = %s" for c in to_update])
                    cur.execute(
                        f"UPDATE marketdata_qh SET {set_clause} WHERE time_qh = %s",
                        list(to_update.values()) + [ts]
                    )
                    upd += 1
        conn.commit()
        if upd > 0:
            log.info(f"  UPDATE: {upd} filas con nulls rellenados")

    return ins, upd


def log_pipeline_db(conn, target, intento, ins, upd, status, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                f"pipeline_diario_intento_{intento}",
                target, target,
                ins + upd,
                status,
                mensaje,
                round(duracion, 2)
            ))
        conn.commit()
    except Exception as e:
        log.error(f"  Error al registrar en pipeline_log: {e}")
        conn.rollback()


# ── Logica principal de un intento ────────────────────────────────────────────

def ejecutar_intento(target: date, intento: int, headers: dict, db_config: dict, log) -> bool:
    """
    Ejecuta un intento de carga para el dia target.
    Devuelve True si el dia queda completo, False si hay que reintentar.
    """
    t0 = time.time()
    log.info(f"{'='*60}")
    log.info(f"INTENTO {intento} — {target} — {datetime.now().strftime('%H:%M:%S')}")
    log.info(f"{'='*60}")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error de conexion a BD: {e}")
        return False

    # Estado inicial
    status_ini = get_day_status(conn, target, log)

    if status_ini["es_completo"]:
        log.info(f"  Dia {target} ya completo en BD — nada que hacer")
        conn.close()
        return True

    # Descarga de ESIOS
    df = fetch_day(headers, target, log)

    ins, upd = 0, 0
    if df is not None and not df.empty:
        try:
            ins, upd = upsert_day(conn, df, target, log)
        except Exception as e:
            log.error(f"  Error en upsert: {e}")
            conn.rollback()

    # Estado final
    status_fin = get_day_status(conn, target, log)
    duracion   = time.time() - t0
    es_completo = status_fin["es_completo"]

    status_str = "ok" if es_completo else "parcial"
    mensaje    = (f"Intento {intento}: {ins} insertadas, {upd} actualizadas, "
                  f"{status_fin['completitud']:.1f}% completo, {duracion:.0f}s")

    log.info(f"  Resultado: {mensaje}")
    log_pipeline_db(conn, target, intento, ins, upd, status_str, mensaje, duracion, log)
    conn.close()

    if es_completo:
        log.info(f"  ✅ Dia {target} COMPLETO tras intento {intento}")
    else:
        log.warning(f"  ⚠️  Dia {target} incompleto — se reintentara en {PAUSA_REINTENTO_MIN} min")

    return es_completo


# ── Main con reintentos ────────────────────────────────────────────────────────

def run(target: date):
    log = setup_logger(target)
    log.info(f"Pipeline diario arrancado para {target}")
    log.info(f"Max reintentos: {MAX_HORAS_REINTENTO}h | Pausa: {PAUSA_REINTENTO_MIN} min")

    try:
        headers, db_config = load_config()
    except Exception as e:
        log.error(f"Error cargando credenciales: {e}")
        return

    max_intentos = (MAX_HORAS_REINTENTO * 60) // PAUSA_REINTENTO_MIN
    intento      = 1

    while intento <= max_intentos:
        completo = ejecutar_intento(target, intento, headers, db_config, log)

        if completo:
            log.info(f"Pipeline finalizado con exito tras {intento} intento(s)")
            break

        if intento >= max_intentos:
            log.error(f"Maximo de intentos alcanzado ({max_intentos}) — pipeline abortado")
            break

        log.info(f"Esperando {PAUSA_REINTENTO_MIN} minutos antes del siguiente intento...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    log.info("Pipeline diario finalizado")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline diario ESIOS → PostgreSQL")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    parser.add_argument("--dias",  type=int, default=1, help="Numero de dias hacia atras")
    args = parser.parse_args()

    if args.fecha:
        fechas = [date.fromisoformat(args.fecha)]
    else:
        fechas = [date.today() - timedelta(days=i) for i in range(1, args.dias + 1)]

    for f in fechas:
        run(f)
