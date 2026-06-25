"""
TFM Energia UCM — ENTSO-E Daily Pipeline v2
Descarga automaticamente los datos reales de ENTSO-E:
  - Dia anterior completo
  - Ultimos 7 dias para rellenar posibles huecos

Logica de completitud:
  - Si faltan <= 2 horas → dia considerado completo (horas no publicadas aun)
  - Si faltan > 2 horas → reintentar cada 10 minutos hasta 12 horas

Cron job (servidor):
    0 22 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/entsoe_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_entsoe.log 2>&1

Log detallado:
    /home/ubuntu/scripts/logs/entsoe_pipeline_YYYY-MM-DD.log
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_HORAS_REINTENTO  = 12
PAUSA_REINTENTO_MIN  = 10
PAUSA_API_SEC        = 1.0
HORAS_TOLERANCIA     = 2    # Si faltan <= 2 horas → dia completo
DIAS_REVISION        = 7    # Revisar ultimos N dias para rellenar huecos
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

COUNTRY    = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"
TIMEZONE   = "Europe/Madrid"
KEY_COLS   = ["actual_load_mw", "solar_mw", "wind_mw", "nuclear_mw"]

GEN_MAPPING = {
    "solar_mw":                 [("Solar", "Actual Aggregated")],
    "wind_mw":                  [("Wind Onshore", "Actual Aggregated")],
    "nuclear_mw":               [("Nuclear", "Actual Aggregated")],
    "ccgt_mw":                  [("Fossil Gas", "Actual Aggregated")],
    "coal_mw":                  [("Fossil Hard coal", "Actual Aggregated")],
    "biomass_mw":               [("Biomass", "Actual Aggregated")],
    "waste_mw":                 [("Waste", "Actual Aggregated")],
    "other_generation_mw":      [("Other", "Actual Aggregated"),
                                  ("Other renewable", "Actual Aggregated")],
    "hydro_mw":                 [("Hydro Water Reservoir", "Actual Aggregated"),
                                  ("Hydro Run-of-river and poundage", "Actual Aggregated")],
    "pumping_generation_mw":    [("Hydro Pumped Storage", "Actual Aggregated"),
                                  ("Energy storage", "Actual Aggregated")],
    "pumping_consumption_mw":   [("Hydro Pumped Storage", "Actual Consumption"),
                                  ("Energy storage", "Actual Consumption")],
    "cogeneration_mw":          [("Fossil Oil", "Actual Aggregated")],
}

ALL_COLS = [
    "datetime_utc", "datetime_local",
    "actual_load_mw",
    "solar_mw", "wind_mw", "nuclear_mw", "ccgt_mw", "coal_mw",
    "biomass_mw", "waste_mw", "hydro_mw", "cogeneration_mw",
    "other_generation_mw", "pumping_generation_mw", "pumping_consumption_mw",
    "renewable_generation_mw", "thermal_generation_mw",
    "residual_demand_mw", "net_load_mw",
    "flow_es_fr_mw", "flow_fr_es_mw", "net_flow_fr_mw",
    "flow_es_pt_mw", "flow_pt_es_mw", "net_flow_pt_mw",
    "net_flow_total_mw",
]

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"entsoe_pipeline_{target_date}.log"
    logger = logging.getLogger(f"entsoe_pipeline_{target_date}")
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

# ── ENTSO-E fetch ──────────────────────────────────────────────────────────────

def to_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(str(d), tz=TIMEZONE)


def resample_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_day(client, target: date, log) -> pd.DataFrame | None:
    ts_start = to_ts(target)
    ts_end   = to_ts(target + timedelta(days=1))
    frames   = {}

    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        frames["actual_load_mw"] = resample_hourly(df["Actual Load"])
    except Exception as e:
        log.warning(f"    actual_load: {e}")
    time.sleep(PAUSA_API_SEC)

    try:
        df_gen = client.query_generation(COUNTRY, start=ts_start, end=ts_end)
        for col, src_cols in GEN_MAPPING.items():
            values = None
            for src_col in src_cols:
                if src_col in df_gen.columns:
                    v = df_gen[src_col].fillna(0)
                    values = v if values is None else values + v
            if values is not None:
                frames[col] = resample_hourly(values)
    except Exception as e:
        log.warning(f"    generation: {e}")
    time.sleep(PAUSA_API_SEC)

    for (c_from, c_to, col) in [
        (COUNTRY, COUNTRY_FR, "flow_es_fr_mw"),
        (COUNTRY_FR, COUNTRY, "flow_fr_es_mw"),
        (COUNTRY, COUNTRY_PT, "flow_es_pt_mw"),
        (COUNTRY_PT, COUNTRY, "flow_pt_es_mw"),
    ]:
        try:
            df_flow = client.query_crossborder_flows(c_from, c_to, start=ts_start, end=ts_end)
            frames[col] = resample_hourly(df_flow)
        except Exception as e:
            log.warning(f"    flow {c_from}→{c_to}: {e}")
        time.sleep(PAUSA_API_SEC)

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime_utc"
    df = df.reset_index()
    df["datetime_local"] = df["datetime_utc"].dt.tz_convert(TIMEZONE)

    renew_cols = [c for c in ["solar_mw","wind_mw","hydro_mw","biomass_mw",
                               "waste_mw","pumping_generation_mw"] if c in df.columns]
    if renew_cols:
        df["renewable_generation_mw"] = df[renew_cols].fillna(0).sum(axis=1)

    thermal_cols = [c for c in ["ccgt_mw","coal_mw","cogeneration_mw","other_generation_mw"] if c in df.columns]
    if thermal_cols:
        df["thermal_generation_mw"] = df[thermal_cols].fillna(0).sum(axis=1)

    if "actual_load_mw" in df.columns:
        ren = df.get("solar_mw", pd.Series(0, index=df.index)).fillna(0)
        win = df.get("wind_mw",  pd.Series(0, index=df.index)).fillna(0)
        df["residual_demand_mw"] = df["actual_load_mw"].fillna(0) - ren - win

    if "flow_es_fr_mw" in df.columns and "flow_fr_es_mw" in df.columns:
        df["net_flow_fr_mw"] = df["flow_fr_es_mw"].fillna(0) - df["flow_es_fr_mw"].fillna(0)
    if "flow_es_pt_mw" in df.columns and "flow_pt_es_mw" in df.columns:
        df["net_flow_pt_mw"] = df["flow_pt_es_mw"].fillna(0) - df["flow_es_pt_mw"].fillna(0)
    if "net_flow_fr_mw" in df.columns and "net_flow_pt_mw" in df.columns:
        df["net_flow_total_mw"] = df["net_flow_fr_mw"].fillna(0) + df["net_flow_pt_mw"].fillna(0)
    if "actual_load_mw" in df.columns and "net_flow_total_mw" in df.columns:
        df["net_load_mw"] = df["actual_load_mw"].fillna(0) - df["net_flow_total_mw"].fillna(0)

    return df

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_day_status(conn, target: date, log) -> dict:
    """
    Devuelve estado del dia en BD.
    Dia completo si: horas_en_bd >= (24 - HORAS_TOLERANCIA)
    """
    null_check = " OR ".join([f"{c} IS NULL" for c in KEY_COLS])
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM entsoe_data WHERE datetime_utc::date = %s", (target,))
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT COUNT(*) FROM entsoe_data
            WHERE datetime_utc::date = %s AND NOT ({null_check})
        """, (target,))
        completas = cur.fetchone()[0]

    horas_faltantes = 24 - total
    es_completo = horas_faltantes <= HORAS_TOLERANCIA and completas >= (24 - HORAS_TOLERANCIA)
    pct = total / 24 * 100

    log.info(f"  [{target}] {total}/24 horas | {completas} completas | {pct:.0f}% "
             f"| faltan {horas_faltantes}h | {'✅ COMPLETO' if es_completo else '⚠️ incompleto'}")

    return {
        "total": total,
        "completas": completas,
        "horas_faltantes": horas_faltantes,
        "es_completo": es_completo,
        "pct": pct,
    }


def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    ins, upd = 0, 0
    data_cols = [c for c in ALL_COLS if c not in ("datetime_utc", "datetime_local")]

    with conn.cursor() as cur:
        cur.execute("SELECT datetime_utc FROM entsoe_data WHERE datetime_utc::date = %s", (target,))
        existing = {row[0] for row in cur.fetchall()}

    # INSERT nuevas horas
    df_new = df[~df["datetime_utc"].isin(existing)]
    if not df_new.empty:
        cols = [c for c in ALL_COLS if c in df_new.columns]
        records = [tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
                   for _, row in df_new.iterrows()]
        sql = f"INSERT INTO entsoe_data ({', '.join(cols)}) VALUES %s ON CONFLICT (datetime_utc) DO NOTHING"
        with conn.cursor() as cur:
            execute_values(cur, sql, records, page_size=500)
        conn.commit()
        ins = len(records)

    # UPDATE horas existentes con nulls
    df_exist = df[df["datetime_utc"].isin(existing)]
    if not df_exist.empty:
        cols_str = ", ".join([c for c in data_cols if c in df_exist.columns])
        with conn.cursor() as cur:
            for _, row in df_exist.iterrows():
                ts = row["datetime_utc"]
                cur.execute(f"SELECT {cols_str} FROM entsoe_data WHERE datetime_utc = %s", (ts,))
                db_row = cur.fetchone()
                if not db_row:
                    continue
                cols_list = [c for c in data_cols if c in row.index]
                to_update = {col: row[col] for i, col in enumerate(cols_list)
                            if db_row[i] is None and not pd.isna(row.get(col))}
                if to_update:
                    set_clause = ", ".join([f"{c} = %s" for c in to_update])
                    cur.execute(f"UPDATE entsoe_data SET {set_clause}, updated_at = now() WHERE datetime_utc = %s",
                               list(to_update.values()) + [ts])
                    upd += 1
        conn.commit()

    if ins > 0: log.info(f"    INSERT: {ins} filas nuevas")
    if upd > 0: log.info(f"    UPDATE: {upd} filas actualizadas")
    return ins, upd


def log_pipeline_db(conn, target, intento, ins, upd, status, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (f"entsoe_daily_{target}_intento_{intento}", target, target,
                  ins + upd, status, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()

# ── Procesar un dia con reintentos ─────────────────────────────────────────────

def procesar_dia_con_reintentos(target: date, client, db_config: dict, log) -> bool:
    """
    Procesa un dia con logica de reintentos.
    Retorna True si el dia queda completo o con tolerancia aceptable.
    """
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

        # Si ya esta completo, salir
        if status["es_completo"]:
            log.info(f"  ✅ Dia {target} completo (tolerancia {HORAS_TOLERANCIA}h)")
            log_pipeline_db(conn, target, intento, 0, 0, "ok",
                          f"Ya completo — {status['total']}/24h", time.time()-t0, log)
            conn.close()
            return True

        # Descargar y cargar
        df = fetch_day(client, target, log)
        ins, upd = 0, 0
        if df is not None and not df.empty:
            try:
                ins, upd = upsert_day(conn, df, target, log)
            except Exception as e:
                log.error(f"  Error upsert: {e}")
                conn.rollback()

        # Estado final del intento
        status_fin = get_day_status(conn, target, log)
        duracion   = time.time() - t0
        es_completo = status_fin["es_completo"]
        estado_str  = "ok" if es_completo else "parcial"
        mensaje = (f"Intento {intento}: {ins} insert, {upd} update, "
                  f"{status_fin['total']}/24h, {status_fin['horas_faltantes']}h faltantes")

        log_pipeline_db(conn, target, intento, ins, upd, estado_str, mensaje, duracion, log)
        conn.close()

        if es_completo:
            log.info(f"  ✅ Dia {target} completo tras intento {intento}")
            return True

        if intento >= max_intentos:
            log.error(f"  ❌ Max intentos alcanzado para {target} — {status_fin['horas_faltantes']}h faltantes")
            return False

        log.info(f"  Esperando {PAUSA_REINTENTO_MIN} min para siguiente intento...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    return False


def revisar_semana(client, db_config: dict, log):
    """Revisa los ultimos DIAS_REVISION dias y rellena huecos sin reintentos."""
    hoy = date.today()
    log.info(f"\n--- Revision ultimos {DIAS_REVISION} dias ---")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD en revision semanal: {e}")
        return

    for i in range(2, DIAS_REVISION + 1):
        dia = hoy - timedelta(days=i)
        status = get_day_status(conn, dia, log)

        if status["es_completo"]:
            continue

        log.info(f"  Rellenando huecos en {dia} ({status['horas_faltantes']}h faltantes)...")
        df = fetch_day(client, dia, log)
        if df is not None and not df.empty:
            try:
                ins, upd = upsert_day(conn, df, dia, log)
                log.info(f"  {dia}: {ins} insert, {upd} update")
            except Exception as e:
                log.error(f"  Error {dia}: {e}")
                conn.rollback()

    conn.close()
    log.info("--- Revision semanal completada ---\n")

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    hoy  = date.today()
    ayer = hoy - timedelta(days=1)
    log  = setup_logger(hoy)

    log.info("=" * 55)
    log.info(f"ENTSO-E Pipeline diario — {hoy}")
    log.info(f"Tolerancia: <= {HORAS_TOLERANCIA} horas faltantes = completo")
    log.info(f"Revision ultimos {DIAS_REVISION} dias para huecos")
    log.info("=" * 55)

    try:
        _, db_config = load_config()
        import json
        creds  = json.load(open(Path(__file__).parent / "credentials.json"))
        client = EntsoePandasClient(api_key=creds["entsoe_token"])
    except Exception as e:
        log.error(f"Error credenciales: {e}")
        return

    # PASO 1 — Procesar ayer con reintentos
    log.info(f"\n=== PASO 1: Dia principal — {ayer} ===")
    procesar_dia_con_reintentos(ayer, client, db_config, log)

    # PASO 2 — Revisar ultimos 7 dias para huecos
    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    revisar_semana(client, db_config, log)

    log.info("\nPipeline ENTSO-E finalizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ENTSO-E daily pipeline")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    args = parser.parse_args()

    if args.fecha:
        # Modo manual — procesa fecha concreta sin reintentos ni revision semanal
        import json
        _, db_config = load_config()
        creds  = json.load(open(Path(__file__).parent / "credentials.json"))
        client = EntsoePandasClient(api_key=creds["entsoe_token"])
        target = date.fromisoformat(args.fecha)
        log    = setup_logger(target)
        log.info(f"Modo manual — procesando {target}")
        procesar_dia_con_reintentos(target, client, db_config, log)
    else:
        run()
