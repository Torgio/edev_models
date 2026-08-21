"""
TFM Energia UCM — ENTSO-E Daily Pipeline v3
Descarga automaticamente los datos reales de ENTSO-E del dia anterior.

Cambios v3 (05/08/2026) — adaptacion a la division de entsoe_data en dos tablas:
  - ESCRITURA DOBLE:
      entsoe_gen_data   -> generacion por tecnologia (16 columnas base)
      entsoe_load_inter -> demanda + flujos de interconexion (5 columnas base)
    La tabla entsoe_data queda como residuo historico y ya no se escribe.
  - GEN_MAPPING actualizado a los nombres nuevos. Correcciones de fondo:
      * cogeneration_mw mapeaba Fossil Oil (B06) -> renombrada oil_mw
      * ccgt_mw -> gas_mw (B04 incluye la cogeneracion de gas, que en España
        no tiene codigo PSR propio segun el Anexo II del P.O. 3.1)
      * hydro_mw separada en hydro_reservoir_mw (B12, despachable, marca
        marginal) y hydro_run_river_mw (B11, no despachable)
      * other_generation_mw (mezclaba B15+B20 y se sumaba 100% a termica)
        separada en other_renewable_mw (B15) y other_thermal_mw (B20)
      * nombres cortos: pumping_gen/cons, battery_gen/cons
  - Las columnas derivadas YA NO se calculan aqui: son GENERATED ALWAYS AS
    STORED en PostgreSQL (total_hydro_mw, total_renew_mw, total_thermal_mw,
    net_load_mw, net_flow_*_mw, total_net_flow_mw). PostgreSQL las mantiene
    sola y no pueden desincronizarse en un recalculo.
  - Criterio de agregacion validado contra REE (nota de prensa enero 2025):
    la turbinacion de bombeo dejo de considerarse generacion y pasa a
    almacenamiento, por lo que pumping_gen_mw y battery_gen_mw quedan FUERA
    de total_renew_mw.
  - Eliminada datetime_local (redundante: la BD esta en Europe/Madrid).
  - ESPORADICOS ampliado: coal_mw esta vacia desde 2021 por el cierre del
    carbon peninsular y oil_mw es residual (~30 MW). Sin estar aqui, el
    pipeline reintentaba 72 veces al dia buscando datos que no existen.
  - Guardarrail: loguea las tecnologias que ENTSO-E devuelve y no estan
    mapeadas, en vez de descartarlas en silencio. Es el fallo que hizo
    perder 10 de las 21 tecnologias del catalogo sin enterarse.

De los 21 codigos PSR del catalogo ENTSO-E la zona ES solo reporta 13
(verificado empiricamente con df.sum() en feb-2020, feb-2021 y jul-2026).
Nunca aparecen lignito B02, gas de coqueria B03, pizarra B07, turba B08,
geotermica B09, marina B13 ni eolica marina B18: se plegan en columnas que
si tienen dato, para capturarlas si algun dia aparecen sin tocar el esquema.

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

TABLA_GEN  = "entsoe_gen_data"
TABLA_LOAD = "entsoe_load_inter"

# ── Mapeo de tecnologias ───────────────────────────────────────────────────────

GEN_MAPPING = {
    # No despachables
    "solar_mw":            [("Solar", "Actual Aggregated")],                       # B16 (FV + termosolar)
    "wind_mw":             [("Wind Onshore", "Actual Aggregated"),                 # B19
                            ("Wind Offshore", "Actual Aggregated")],              # B18 (no existe en ES)
    "hydro_run_river_mw":  [("Hydro Run-of-river and poundage", "Actual Aggregated")],  # B11

    # Hidraulica despachable
    "hydro_reservoir_mw":  [("Hydro Water Reservoir", "Actual Aggregated")],       # B12

    # Almacenamiento (fuera de total_renew_mw por criterio REE enero 2025)
    "pumping_gen_mw":      [("Hydro Pumped Storage", "Actual Aggregated")],        # B10 gen
    "pumping_cons_mw":     [("Hydro Pumped Storage", "Actual Consumption")],       # B10 cons
    "battery_gen_mw":      [("Energy storage", "Actual Aggregated")],              # B25 gen
    "battery_cons_mw":     [("Energy storage", "Actual Consumption")],             # B25 cons

    # Otras renovables
    "biomass_mw":          [("Biomass", "Actual Aggregated")],                     # B01
    "waste_mw":            [("Waste", "Actual Aggregated")],                       # B17
    "other_renewable_mw":  [("Other renewable", "Actual Aggregated"),              # B15
                            ("Geothermal", "Actual Aggregated"),                   # B09 (no existe en ES)
                            ("Marine", "Actual Aggregated")],                      # B13 (no existe en ES)

    # Base
    "nuclear_mw":          [("Nuclear", "Actual Aggregated")],                     # B14

    # Fosiles
    "gas_mw":              [("Fossil Gas", "Actual Aggregated")],                  # B04 (incluye cogeneracion)
    "coal_mw":             [("Fossil Hard coal", "Actual Aggregated"),             # B05 (cerrado desde 2021)
                            ("Fossil Brown coal/Lignite", "Actual Aggregated")],  # B02 (nunca reportado en ES)
    "oil_mw":              [("Fossil Oil", "Actual Aggregated")],                  # B06 (residual ~30 MW)
    "other_thermal_mw":    [("Other", "Actual Aggregated"),                        # B20
                            ("Fossil Coal-derived gas", "Actual Aggregated"),      # B03 (no reportado en ES)
                            ("Fossil Oil shale", "Actual Aggregated"),             # B07 (no existe en ES)
                            ("Fossil Peat", "Actual Aggregated")],                 # B08 (no existe en ES)
}

# Columnas base escribibles de cada tabla (las GENERATED las calcula PostgreSQL)
GEN_COLS = [
    "solar_mw", "wind_mw",
    "hydro_run_river_mw", "hydro_reservoir_mw",
    "pumping_gen_mw", "pumping_cons_mw",
    "battery_gen_mw", "battery_cons_mw",
    "biomass_mw", "waste_mw", "other_renewable_mw",
    "nuclear_mw",
    "gas_mw", "coal_mw", "oil_mw", "other_thermal_mw",
]

LOAD_COLS = [
    "actual_load_mw",
    "flow_es_fr_mw", "flow_fr_es_mw",
    "flow_es_pt_mw", "flow_pt_es_mw",
    "ntc_imp_fr_mw", "ntc_exp_fr_mw",
    "ntc_imp_pt_mw", "ntc_exp_pt_mw",
]

# Marruecos no es miembro de ENTSO-E — sus columnas quedan NULL permanentemente.
# Las NTC se cargan desde ENTSO-E como backup; fuente primaria es ESIOS (488-494).
# de ESIOS (indicadores 488-494), donde ya hay historico desde 2020.
# Quedan NULL de momento — por eso no estan en LOAD_COLS.

# Criticos: si faltan, se reintenta. Uno vive en cada tabla.
CRITICOS_GEN  = {"solar_mw", "wind_mw", "nuclear_mw", "gas_mw"}
CRITICOS_LOAD = {"actual_load_mw"}

# Esporadicos: null es un resultado valido, no se reintenta por ellos.
ESPORADICOS = {
    "coal_mw",             # carbon peninsular cerrado desde 2021
    "oil_mw",              # fuel residual (~30 MW)
    "biomass_mw",
    "waste_mw",
    "hydro_run_river_mw",
    "other_renewable_mw",  # ~60 MW
    "other_thermal_mw",    # ~15 MW
    "pumping_gen_mw",      # solo cuando hay bombeo turbinando
    "pumping_cons_mw",     # solo cuando hay bombeo consumiendo
    "battery_gen_mw",      # B25 solo se reporta desde finales de 2025
    "battery_cons_mw",
}

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
    Rango de timestamps para la peticion a ENTSO-E.
    Pide target-1 hasta target+1 en hora española para capturar todas las
    horas del dia en UTC (desfase CET/CEST).
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
    """
    Media horaria. IMPORTANTE: resample ANTES de filtrar al dia objetivo.
    Los datos nativos de ENTSO-E son cuarto-horarios desde octubre de 2025
    (MTU15); si se filtra primero, expected_hours_utc() descarta las muestras
    de :15/:30/:45 y queda una sola muestra por hora en vez del promedio real
    (verificado: diferencia media de 172 MW y maxima de 633 MW en el flujo
    FR->ES del 30-jul-2026).
    Media y no suma porque estos valores son POTENCIA (MW).
    """
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_day(client, target: date, log) -> pd.DataFrame | None:
    ts_start, ts_end = to_ts_range(target)
    frames = {}

    # ── Carga real ──
    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        resampled = resample_hourly(df["Actual Load"])
        frames["actual_load_mw"] = filter_to_target_day(
            resampled.to_frame(name="actual_load_mw"), target)["actual_load_mw"]
        log.debug(f"    actual_load: {len(frames['actual_load_mw'])} horas")
    except Exception as e:
        log.warning(f"    actual_load: {e}")
    time.sleep(PAUSA_API_SEC)

    # ── Generacion por tecnologia ──
    try:
        df_gen = client.query_generation(COUNTRY, start=ts_start, end=ts_end)

        for col, src_cols in GEN_MAPPING.items():
            values = None
            for src_col in src_cols:
                if src_col in df_gen.columns:
                    v = df_gen[src_col].fillna(0)
                    values = v if values is None else values + v
            if values is not None:
                resampled = resample_hourly(values)
                frames[col] = filter_to_target_day(resampled.to_frame(name=col), target)[col]

        # Guardarrail: tecnologias que ENTSO-E devuelve y no estan mapeadas.
        # Sin esto se descartarian en silencio, que es el fallo original.
        mapeadas = {src for lista in GEN_MAPPING.values() for src in lista}
        huerfanas = [c for c in df_gen.columns if c not in mapeadas]
        if huerfanas:
            log.warning(f"    [SIN MAPEAR] tecnologias nuevas de ENTSO-E: {huerfanas}")

    except Exception as e:
        log.warning(f"    generation: {e}")
    time.sleep(PAUSA_API_SEC)

    # ── Flujos de interconexion ──
    for (c_from, c_to, col) in [
        (COUNTRY, COUNTRY_FR, "flow_es_fr_mw"),
        (COUNTRY_FR, COUNTRY, "flow_fr_es_mw"),
        (COUNTRY, COUNTRY_PT, "flow_es_pt_mw"),
        (COUNTRY_PT, COUNTRY, "flow_pt_es_mw"),
    ]:
        try:
            df_flow = client.query_crossborder_flows(c_from, c_to, start=ts_start, end=ts_end)
            resampled = resample_hourly(df_flow)
            frames[col] = filter_to_target_day(resampled.to_frame(name=col), target)[col]
        except Exception as e:
            # No incluir la URL: lleva el securityToken en texto plano
            msg = str(e).split("securityToken")[0]
            log.warning(f"    flow {c_from}→{c_to}: {msg}")
        time.sleep(PAUSA_API_SEC)

# ── NTC de interconexion (backup ENTSO-E, fuente primaria es ESIOS) ──
    for (c_from, c_to, col) in [
        (COUNTRY_FR, COUNTRY, "ntc_imp_fr_mw"),
        (COUNTRY, COUNTRY_FR, "ntc_exp_fr_mw"),
        (COUNTRY_PT, COUNTRY, "ntc_imp_pt_mw"),
        (COUNTRY, COUNTRY_PT, "ntc_exp_pt_mw"),
    ]:
        try:
            df_ntc = client.query_net_transfer_capacity_dayahead(
                c_from, c_to, start=ts_start, end=ts_end)
            resampled = resample_hourly(df_ntc)
            frames[col] = filter_to_target_day(
                resampled.to_frame(name=col), target)[col]
        except Exception as e:
            msg = str(e).split("securityToken")[0]
            log.warning(f"    NTC {c_from}→{c_to}: {msg}")
        time.sleep(PAUSA_API_SEC)

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime"
    df = df.round(2)
    return df.reset_index()

    
# ── BD helpers ─────────────────────────────────────────────────────────────────

def _estado_tabla(cur, tabla: str, cols: list, criticos: set,
                  start_utc, end_utc, n_expected: int, log) -> dict:
    """Estado de una tabla concreta para el dia: horas, criticos y nulls."""
    cur.execute(f"""
        SELECT COUNT(*) FROM {tabla}
        WHERE datetime >= %s AND datetime <= %s
    """, (start_utc, end_utc))
    total = cur.fetchone()[0]

    criticos_ok = True
    for col in criticos:
        cur.execute(f"""
            SELECT COUNT(*) FROM {tabla}
            WHERE datetime >= %s AND datetime <= %s AND {col} IS NOT NULL
        """, (start_utc, end_utc))
        n = cur.fetchone()[0]
        if n < n_expected:
            criticos_ok = False
            log.warning(f"    [CRITICO] {tabla}.{col}: {n}/{n_expected}h")

    nulls_recuperables = {}
    nulls_esporadicos  = {}
    for col in cols:
        cur.execute(f"""
            SELECT COUNT(*) FROM {tabla}
            WHERE datetime >= %s AND datetime <= %s AND {col} IS NULL
        """, (start_utc, end_utc))
        n = cur.fetchone()[0]
        if n > 0:
            if col in ESPORADICOS:
                nulls_esporadicos[f"{tabla}.{col}"] = n
            elif col not in criticos:
                nulls_recuperables[f"{tabla}.{col}"] = n

    return {
        "total": total,
        "criticos_ok": criticos_ok,
        "nulls_recuperables": nulls_recuperables,
        "nulls_esporadicos": nulls_esporadicos,
    }


def get_day_status(conn, target: date, log) -> dict:
    """
    Estado combinado del dia en LAS DOS tablas.
    El dia se considera completo cuando ambas tienen sus horas y sus criticos:
    el pipeline pide generacion y carga en la misma tanda, asi que separar los
    estados no ahorraria peticiones.
    """
    expected   = expected_hours_utc(target)
    n_expected = len(expected)
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        st_gen  = _estado_tabla(cur, TABLA_GEN,  GEN_COLS,  CRITICOS_GEN,
                                start_utc, end_utc, n_expected, log)
        st_load = _estado_tabla(cur, TABLA_LOAD, LOAD_COLS, CRITICOS_LOAD,
                                start_utc, end_utc, n_expected, log)

    total_gen  = st_gen["total"]
    total_load = st_load["total"]
    criticos_ok = st_gen["criticos_ok"] and st_load["criticos_ok"]

    nulls_recuperables = {**st_gen["nulls_recuperables"], **st_load["nulls_recuperables"]}
    nulls_esporadicos  = {**st_gen["nulls_esporadicos"],  **st_load["nulls_esporadicos"]}

    es_completo = (total_gen >= n_expected and total_load >= n_expected and criticos_ok)

    log.info(f"  [{target}] gen={total_gen}/{n_expected}h load={total_load}/{n_expected}h | "
             f"criticos={'✅' if criticos_ok else '❌'} | "
             f"{'✅ COMPLETO' if es_completo else '⚠️ incompleto'}")
    if nulls_recuperables:
        log.warning(f"    [nulls recuperables] {list(nulls_recuperables.keys())}")
    if nulls_esporadicos:
        log.debug(f"    [nulls esporadicos — OK] {list(nulls_esporadicos.keys())}")

    return {
        "total_gen": total_gen,
        "total_load": total_load,
        "n_expected": n_expected,
        "criticos_ok": criticos_ok,
        "es_completo": es_completo,
        "nulls_recuperables": nulls_recuperables,
        "nulls_esporadicos": nulls_esporadicos,
    }


def _upsert_tabla(conn, df: pd.DataFrame, tabla: str, cols: list,
                  target: date, log) -> tuple[int, int]:
    """
    Inserta horas nuevas y rellena NULLs en las existentes, para una tabla.
    Solo escribe columnas base: las GENERATED las mantiene PostgreSQL.
    """
    presentes = [c for c in cols if c in df.columns]
    if not presentes:
        return 0, 0

    ins = upd = 0
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT datetime FROM {tabla}
            WHERE datetime >= %s AND datetime <= %s
        """, (start_utc, end_utc))
        existing = {row[0] for row in cur.fetchall()}

    # INSERT de horas nuevas
    df_new = df[~df["datetime"].isin(existing)]
    if not df_new.empty:
        insert_cols = ["datetime"] + presentes
        records = [tuple(None if pd.isna(row.get(c)) else row.get(c) for c in insert_cols)
                   for _, row in df_new.iterrows()]
        sql = (f"INSERT INTO {tabla} ({', '.join(insert_cols)}) VALUES %s "
               f"ON CONFLICT (datetime) DO NOTHING")
        with conn.cursor() as cur:
            execute_values(cur, sql, records, page_size=500)
        conn.commit()
        ins = len(records)

    # UPDATE de horas existentes que tengan NULLs
    df_exist = df[df["datetime"].isin(existing)]
    if not df_exist.empty:
        cols_str = ", ".join(presentes)
        with conn.cursor() as cur:
            for _, row in df_exist.iterrows():
                ts = row["datetime"]
                cur.execute(f"SELECT {cols_str} FROM {tabla} WHERE datetime = %s", (ts,))
                db_row = cur.fetchone()
                if not db_row:
                    continue
                to_update = {col: row[col] for i, col in enumerate(presentes)
                             if db_row[i] is None and not pd.isna(row.get(col))}
                if to_update:
                    set_clause = ", ".join([f"{c} = %s" for c in to_update])
                    cur.execute(
                        f"UPDATE {tabla} SET {set_clause}, updated_at = now() "
                        f"WHERE datetime = %s",
                        list(to_update.values()) + [ts])
                    upd += 1
        conn.commit()

    if ins or upd:
        log.info(f"    {tabla}: {ins} insert, {upd} update")
    return ins, upd


def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    """Escritura doble: generacion a entsoe_gen_data, demanda y flujos a entsoe_load_inter."""
    ins_g, upd_g = _upsert_tabla(conn, df, TABLA_GEN,  GEN_COLS,  target, log)
    ins_l, upd_l = _upsert_tabla(conn, df, TABLA_LOAD, LOAD_COLS, target, log)
    return ins_g + ins_l, upd_g + upd_l


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

        if status["es_completo"] and not status["nulls_recuperables"]:
            log.info(f"  ✅ Dia {target} completo")
            if status["nulls_esporadicos"]:
                log.info(f"  Esporadicos con nulls (correcto): "
                         f"{list(status['nulls_esporadicos'].keys())}")
            log_pipeline_db(conn, target, intento, 0, 0, "ok",
                            f"Ya completo — gen {status['total_gen']}/{status['n_expected']}h, "
                            f"load {status['total_load']}/{status['n_expected']}h",
                            time.time()-t0, log)
            conn.close()
            return True

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
                   f"gen {status_fin['total_gen']}/{status_fin['n_expected']}h, "
                   f"load {status_fin['total_load']}/{status_fin['n_expected']}h")

        log_pipeline_db(conn, target, intento, ins, upd, estado_str, mensaje, duracion, log)
        conn.close()

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
    """Revision inteligente — solo descarga si hay huecos o nulls recuperables."""
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

        if status["es_completo"] and not status["nulls_recuperables"]:
            if status["nulls_esporadicos"]:
                log.info(f"  [{dia}] ✅ Solo esporadicos con nulls — OK")
            continue

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

    log.info("=" * 62)
    log.info(f"ENTSO-E Pipeline diario v3 — {hoy}")
    log.info(f"Tablas: {TABLA_GEN} ({len(GEN_COLS)} cols) + {TABLA_LOAD} ({len(LOAD_COLS)} cols)")
    log.info(f"UTC/hora española | 23/24/25h soportado")
    log.info(f"Criticos gen: {sorted(CRITICOS_GEN)} | load: {sorted(CRITICOS_LOAD)}")
    log.info(f"Derivadas calculadas por PostgreSQL (GENERATED)")
    log.info(f"Revision ultimos {DIAS_REVISION} dias")
    log.info("=" * 62)

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