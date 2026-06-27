"""
TFM Energia UCM — ENTSO-E Daily Pipeline v2
Descarga automaticamente los datos reales de ENTSO-E del dia anterior.

Mejoras v2:
  - Correccion UTC/hora española (ZoneInfo Europe/Madrid)
  - Peticion target-1 hasta target+1 en UTC — filtra por hora española
  - Soporte dias 23h/24h/25h (cambio de hora)
  - Indicadores esporadicos (bombeo) — null es valido
  - Descarga selectiva — solo si faltan horas
  - Revision 7 dias para rellenar huecos
  - Dos niveles: criticos (carga, solar, viento) y esporadicos (bombeo)

Cron job (servidor):
    0 20 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/entsoe_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_entsoe.log 2>&1

Log detallado:
    /home/ubuntu/scripts/logs/entsoe_pipeline_YYYY-MM-DD.log
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_HORAS_REINTENTO  = 12
PAUSA_REINTENTO_MIN  = 10
PAUSA_API_SEC        = 1.0
DIAS_REVISION        = 7
TZ_SPAIN             = ZoneInfo("Europe/Madrid")

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

COUNTRY    = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"

# Indicadores criticos — si faltan, reintentar
CRITICOS = {"actual_load_mw", "solar_mw", "wind_mw", "nuclear_mw"}

# Indicadores esporadicos — null valido (solo cuando hay operacion)
ESPORADICOS = {
    "pumping_generation_mw",   # solo cuando hay bombeo turbinando
    "pumping_consumption_mw",  # solo cuando hay bombeo consumiendo
    "biomass_mw",              # puede no operar todas las horas
}

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

# ── Helpers UTC/hora española ──────────────────────────────────────────────────

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
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    return start_spain.astimezone(timezone.utc), end_spain.astimezone(timezone.utc)

# ── ENTSO-E fetch ──────────────────────────────────────────────────────────────

def to_ts_range(target: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Devuelve rango de timestamps para peticion ENTSO-E.
    Pide target-1 hasta target+1 en hora española para capturar
    todas las horas del dia en UTC (desfase CET/CEST).
    """
    start = pd.Timestamp(str(target - timedelta(days=1)), tz="Europe/Madrid")
    end   = pd.Timestamp(str(target + timedelta(days=1)), tz="Europe/Madrid")
    return start, end


def filter_to_target_day(df: pd.DataFrame, target: date) -> pd.DataFrame:
    """Filtra un DataFrame para quedarse solo con filas del dia target en hora española."""
    expected = expected_hours_utc(target)
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("UTC")
    return df[df.index.isin(expected)]


def resample_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_day(client, target: date, log) -> pd.DataFrame | None:
    ts_start, ts_end = to_ts_range(target)
    frames = {}

    # Carga real
    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        filtered = filter_to_target_day(df, target)
        frames["actual_load_mw"] = resample_hourly(filtered["Actual Load"])
        log.debug(f"    actual_load: {len(frames['actual_load_mw'])} horas")
    except Exception as e:
        log.warning(f"    actual_load: {e}")
    time.sleep(PAUSA_API_SEC)

    # Generacion por tecnologia
    try:
        df_gen = client.query_generation(COUNTRY, start=ts_start, end=ts_end)
        df_gen_filtered = filter_to_target_day(df_gen, target)
        for col, src_cols in GEN_MAPPING.items():
            values = None
            for src_col in src_cols:
                if src_col in df_gen_filtered.columns:
                    v = df_gen_filtered[src_col].fillna(0)
                    values = v if values is None else values + v
            if values is not None:
                frames[col] = resample_hourly(values)
    except Exception as e:
        log.warning(f"    generation: {e}")
    time.sleep(PAUSA_API_SEC)

    # Flujos interconexion
    for (c_from, c_to, col) in [
        (COUNTRY, COUNTRY_FR, "flow_es_fr_mw"),
        (COUNTRY_FR, COUNTRY, "flow_fr_es_mw"),
        (COUNTRY, COUNTRY_PT, "flow_es_pt_mw"),
        (COUNTRY_PT, COUNTRY, "flow_pt_es_mw"),
    ]:
        try:
            df_flow = client.query_crossborder_flows(c_from, c_to, start=ts_start, end=ts_end)
            filtered = filter_to_target_day(df_flow, target)
            frames[col] = resample_hourly(filtered)
        except Exception as e:
            log.warning(f"    flow {c_from}→{c_to}: {e}")
        time.sleep(PAUSA_API_SEC)

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime_utc"
    df = df.reset_index()
    df["datetime_local"] = df["datetime_utc"].dt.tz_convert("Europe/Madrid")

    # Columnas derivadas
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
    Analiza el estado del dia en BD usando rango UTC calculado desde hora española.
    Soporta 23h/24h/25h. Separa criticos de esporadicos.
    """
    expected   = expected_hours_utc(target)
    n_expected = len(expected)
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM entsoe_data
            WHERE datetime_utc >= %s AND datetime_utc <= %s
        """, (start_utc, end_utc))
        total = cur.fetchone()[0]

        criticos_ok = True
        for col in CRITICOS:
            cur.execute(f"""
                SELECT COUNT(*) FROM entsoe_data
                WHERE datetime_utc >= %s AND datetime_utc <= %s AND {col} IS NOT NULL
            """, (start_utc, end_utc))
            n = cur.fetchone()[0]
            if n < n_expected:
                criticos_ok = False
                log.warning(f"    [CRITICO] {col}: {n}/{n_expected}h")

        # Nulls recuperables (no criticos, no esporadicos)
        nulls_recuperables = {}
        nulls_esporadicos  = {}
        data_cols = [c for c in ALL_COLS if c not in ("datetime_utc", "datetime_local")]
        for col in data_cols:
            cur.execute(f"""
                SELECT COUNT(*) FROM entsoe_data
                WHERE datetime_utc >= %s AND datetime_utc <= %s AND {col} IS NULL
            """, (start_utc, end_utc))
            n = cur.fetchone()[0]
            if n > 0:
                if col in ESPORADICOS:
                    nulls_esporadicos[col] = n
                elif col not in CRITICOS:
                    nulls_recuperables[col] = n

    es_completo = total >= n_expected and criticos_ok
    pct = total / n_expected * 100

    log.info(f"  [{target}] {total}/{n_expected}h | {pct:.0f}% | "
             f"criticos={'✅' if criticos_ok else '❌'} | "
             f"{'✅ COMPLETO' if es_completo else '⚠️ incompleto'}")
    if nulls_recuperables:
        log.warning(f"    [nulls recuperables] {list(nulls_recuperables.keys())}")
    if nulls_esporadicos:
        log.debug(f"    [nulls esporadicos — OK] {list(nulls_esporadicos.keys())}")

    return {
        "total": total,
        "n_expected": n_expected,
        "criticos_ok": criticos_ok,
        "es_completo": es_completo,
        "nulls_recuperables": nulls_recuperables,
        "nulls_esporadicos": nulls_esporadicos,
        "pct": pct,
    }


def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    ins, upd = 0, 0
    data_cols = [c for c in ALL_COLS if c not in ("datetime_utc", "datetime_local")]
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT datetime_utc FROM entsoe_data
            WHERE datetime_utc >= %s AND datetime_utc <= %s
        """, (start_utc, end_utc))
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

# ── Procesar dia con reintentos ────────────────────────────────────────────────

def procesar_dia_con_reintentos(target: date, client, db_config: dict, log) -> bool:
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

        # Completo y sin nulls recuperables — nada que hacer
        if status["es_completo"] and not status["nulls_recuperables"]:
            log.info(f"  ✅ Dia {target} completo")
            if status["nulls_esporadicos"]:
                log.info(f"  Esporadicos con nulls (correcto): {list(status['nulls_esporadicos'].keys())}")
            log_pipeline_db(conn, target, intento, 0, 0, "ok",
                          f"Ya completo — {status['total']}/{status['n_expected']}h", time.time()-t0, log)
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

        status_fin  = get_day_status(conn, target, log)
        duracion    = time.time() - t0
        es_completo = status_fin["es_completo"]
        estado_str  = "ok" if es_completo else "parcial"
        mensaje = (f"Intento {intento}: {ins} insert, {upd} update, "
                  f"{status_fin['total']}/{status_fin['n_expected']}h")

        log_pipeline_db(conn, target, intento, ins, upd, estado_str, mensaje, duracion, log)
        conn.close()

        # Criticos incompletos → reintentar
        if not status_fin["criticos_ok"]:
            log.warning(f"  ❌ Criticos incompletos — reintentando en {PAUSA_REINTENTO_MIN} min")
        elif status_fin["nulls_recuperables"]:
            log.warning(f"  ⚠️ Nulls recuperables pendientes — reintentando")
        else:
            log.info(f"  ✅ Dia {target} completo tras intento {intento}")
            return True

        if intento >= max_intentos:
            log.error(f"  ❌ Max intentos alcanzado para {target}")
            return False

        log.info(f"  Esperando {PAUSA_REINTENTO_MIN} min...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    return False


def revisar_semana(client, db_config: dict, log):
    """Revision inteligente — solo descarga si hay nulls recuperables."""
    hoy = date.today()
    log.info(f"\n--- Revision ultimos {DIAS_REVISION} dias ---")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD: {e}")
        return

    for i in range(2, DIAS_REVISION + 1):
        dia = hoy - timedelta(days=i)
        status = get_day_status(conn, dia, log)

        # Solo esporadicos con nulls — OK, no descargar
        if status["es_completo"] and not status["nulls_recuperables"]:
            if status["nulls_esporadicos"]:
                log.info(f"  [{dia}] ✅ Solo esporadicos con nulls — OK")
            continue

        if not status["nulls_recuperables"] and not status["es_completo"]:
            # Horas faltantes pero sin nulls recuperables — intentar descargar
            pass

        log.info(f"  [{dia}] Descargando para rellenar huecos...")
        df = fetch_day(client, dia, log)
        if df is not None and not df.empty:
            try:
                ins, upd = upsert_day(conn, df, dia, log)
                if ins + upd > 0:
                    log.info(f"  [{dia}] {ins} insert, {upd} update")
                else:
                    log.info(f"  [{dia}] Sin cambios")
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
    log.info(f"UTC/hora española | 23/24/25h | Criticos: {CRITICOS}")
    log.info(f"Revision ultimos {DIAS_REVISION} dias")
    log.info("=" * 55)

    try:
        _, db_config = load_config()
        import json
        creds  = json.load(open(Path(__file__).parent / "credentials.json"))
        client = EntsoePandasClient(api_key=creds["entsoe_token"])
    except Exception as e:
        log.error(f"Error credenciales: {e}")
        return

    log.info(f"\n=== PASO 1: Dia principal — {ayer} ===")
    procesar_dia_con_reintentos(ayer, client, db_config, log)

    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    revisar_semana(client, db_config, log)

    log.info("\nPipeline ENTSO-E finalizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ENTSO-E daily pipeline")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    args = parser.parse_args()

    if args.fecha:
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
