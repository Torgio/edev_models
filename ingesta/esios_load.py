"""
TFM Energia UCM — ESIOS Flexible Data Loader v5
Todos los IDs verificados con esios_test_indicators_v2.py el 23/06/2026.

Logica completa:
  PASO 1 — Consulta BD: horas faltantes + horas con nulls
  PASO 2 — INSERT horas nuevas
  PASO 3 — UPDATE columnas null en horas existentes

Credenciales: ingesta/credentials.json (en .gitignore — nunca a GitHub)

╔══════════════════════════════════════════╗
║  CONFIGURACION — editar esta seccion     ║
╚══════════════════════════════════════════╝
Modos:
  "test"  → 7 dias desde START_DATE
  "week"  → semana concreta (WEEK_START)
  "month" → mes completo (YEAR + MONTH)
  "year"  → año completo (YEAR)
  "range" → rango libre (START_DATE + END_DATE)

Usage:
    python esios_load.py
"""

import time
import logging
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from calendar import monthrange

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from config import load_config

# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI             ║
# ╚══════════════════════════════════════════════════════════════╝

MODE  = "range"      # "test" | "week" | "month" | "year" | "range"

YEAR  = 2025        # Año  (usado en: year, month)
MONTH = 1           # Mes  (usado en: month, 1-12)

WEEK_START = "2020-01-06"   # Lunes de semana (usado en: week)
START_DATE = "2026-01-01"   # Inicio (usado en: range / test)
END_DATE   = "2026-06-22"   # Fin    (usado en: range)

# ╚══════════════════════════════════════════════════════════════╝
# ║                  FIN DE CONFIGURACION                       ║
# ╚══════════════════════════════════════════════════════════════╝

# ── Indicadores ESIOS — todos verificados el 23/06/2026 ───────────────────────
# Formato: "columna_bd": (indicator_id, geo_id)
INDICATORS = {
    "price_eur_mwh":         (600,   None),
    "demanda_real_mw":       (1293,  8741),
    "demanda_prev_mw":       (544,   8741),
    "gen_solar_mw":          (1295,  8741),  # ID 552 obsoleto desde jun-2015
    "gen_solar_term_real_mw":(1294,  8741),
    "gen_wind_mw":           (551,   8741),
    "gen_hidro_real_mw":     (546,   8741),
    "gen_nuclear_real_mw":   (549,   8741),
    "gen_ciclocomb_real_mw": (550,   8741),
    "gen_coal_real_mw":      (547,   8741),
    "gen_cogen_real_mw":     (553,   8741),
    "resto_gen_real_mw":     (1297,  8741),  # ID 555 no devuelve datos
    "saldo_francia_mw":      (10045, None),
    "saldo_portugal_mw":     (557,   8741),
    "saldo_portugal_exp_mw": (561,   8741),
    "saldo_marruecos_mw":    (10046, None),
    "gen_solar_prev_mw":     (542,   8741),
    "gen_solar_term_prev_mw":(543,   8741),
    "precio_banda_sec_mwh":  (634,   8741),
    "gen_bombeo_turb_mw":    (1152,  None),
    "cons_bombeo_mw":        (1172,  None),
    "ntc_francia_imp_mw":    (488,   8741),  # IDs 1844/1848/1845 no devuelven datos
    "ntc_francia_exp_mw":    (492,   8741),
    "ntc_portugal_imp_mw":   (489,   8741),
    "ntc_portugal_exp_mw":   (493,   8741),
    "ntc_marruecos_imp_mw":  (490,   8741),
    "ntc_marruecos_exp_mw":  (494,   8741),
    "co2_real_t":            (10355, 8741),  # ID 1391 no devuelve datos
    "gen_libre_co2_mw":      (10006, 8741),
    "pct_gen_libre_co2":     (10033, 8741),
}

ESIOS_BASE = "https://api.esios.ree.es"
CHUNK_DAYS = 7
PAUSE_SEC  = 0.5
BATCH_SIZE = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("esios_load")

# ── Date resolution ────────────────────────────────────────────────────────────

def resolve_dates() -> tuple[date, date]:
    if MODE == "test":
        start = date.fromisoformat(START_DATE)
        return start, start + timedelta(days=6)
    elif MODE == "week":
        start = date.fromisoformat(WEEK_START)
        return start, start + timedelta(days=6)
    elif MODE == "month":
        start = date(YEAR, MONTH, 1)
        return start, date(YEAR, MONTH, monthrange(YEAR, MONTH)[1])
    elif MODE == "year":
        return date(YEAR, 1, 1), date(YEAR, 12, 31)
    elif MODE == "range":
        return date.fromisoformat(START_DATE), date.fromisoformat(END_DATE)
    else:
        raise ValueError(f"Unknown MODE: {MODE}")

# ── BD check ──────────────────────────────────────────────────────────────────

def get_db_status(conn, start: date, end: date) -> tuple[set, set]:
    """Devuelve (horas_existentes, horas_con_nulls)."""
    data_cols = list(INDICATORS.keys())
    null_check = " OR ".join([f"{c} IS NULL" for c in data_cols])
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt   = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)

    with conn.cursor() as cur:
        cur.execute("SELECT time_qh FROM marketdata_qh WHERE time_qh >= %s AND time_qh < %s",
                    (start_dt, end_dt))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"SELECT time_qh FROM marketdata_qh WHERE time_qh >= %s AND time_qh < %s AND ({null_check})",
                    (start_dt, end_dt))
        with_nulls = {row[0] for row in cur.fetchall()}

    return existing, with_nulls


def build_expected_hours(start: date, end: date) -> set:
    hours   = set()
    current = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt  = datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc)
    while current <= end_dt:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def missing_to_chunks(hours: set, chunk_days: int = CHUNK_DAYS) -> list:
    if not hours:
        return []
    days    = sorted({h.date() for h in hours})
    chunks  = []
    cs, pd_ = days[0], days[0]
    for d in days[1:]:
        if (d - pd_).days > 1 or (d - cs).days >= chunk_days:
            chunks.append((cs, pd_))
            cs = d
        pd_ = d
    chunks.append((cs, pd_))
    return chunks

# ── API ────────────────────────────────────────────────────────────────────────

def fetch_indicator(headers, indicator_id, geo_id, start, end):
    url    = f"{ESIOS_BASE}/indicators/{indicator_id}"
    params = {"start_date": f"{start}T00:00:00", "end_date": f"{end}T23:59:59", "time_trunc": "hour"}
    if geo_id is not None:
        params["geo_ids[]"] = geo_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            return None
        df = pd.DataFrame(values)[["datetime_utc", "value"]]
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
        df = df.set_index("datetime_utc")["value"]
        return df[~df.index.duplicated(keep="first")]
    except Exception as e:
        log.warning(f"    Error indicator {indicator_id}: {e}")
        return None


def fetch_chunk(headers, start, end):
    frames = {}
    for col, (ind_id, geo_id) in INDICATORS.items():
        serie = fetch_indicator(headers, ind_id, geo_id, start, end)
        if serie is not None:
            frames[col] = serie
        time.sleep(PAUSE_SEC)
    if not frames:
        return None
    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()

# ── Insert & Update ────────────────────────────────────────────────────────────

ALL_COLS = ["time_qh"] + list(INDICATORS.keys())

def insert_rows(conn, df, missing_hours):
    df_f = df[df["time_qh"].isin(missing_hours)]
    if df_f.empty:
        return 0
    cols = [c for c in ALL_COLS if c in df_f.columns]
    records = [
        tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
        for _, row in df_f.iterrows()
    ]
    sql = f"INSERT INTO marketdata_qh ({', '.join(cols)}) VALUES %s ON CONFLICT (time_qh) DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=BATCH_SIZE)
    conn.commit()
    return len(records)


def update_null_rows(conn, df, hours_with_nulls):
    """Actualiza columna a columna solo donde BD tiene NULL y el nuevo dato tiene valor."""
    if not hours_with_nulls:
        return 0
    data_cols = list(INDICATORS.keys())
    df_n = df[df["time_qh"].isin(hours_with_nulls)]
    if df_n.empty:
        return 0

    updated = 0
    cols_str = ", ".join([c for c in data_cols if c in df_n.columns])

    with conn.cursor() as cur:
        for _, row in df_n.iterrows():
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
                updated += 1

    conn.commit()
    return updated


def log_pipeline(conn, start, end, n, status, message, duration):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, ("esios_load", start, end, n, status, message, round(duration, 2)))
        conn.commit()
    except Exception:
        conn.rollback()

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    start_date, end_date = resolve_dates()
    headers, db_config   = load_config()

    log.info("=" * 60)
    log.info(f"  MODE        : {MODE}")
    log.info(f"  Period      : {start_date} → {end_date}")
    log.info(f"  Indicators  : {len(INDICATORS)}")
    log.info("=" * 60)

    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")

    # PASO 1 — Estado BD
    log.info("Step 1 — Checking DB status...")
    existing, with_nulls = get_db_status(conn, start_date, end_date)
    expected = build_expected_hours(start_date, end_date)
    missing  = expected - existing

    log.info(f"  Expected hours  : {len(expected):,}")
    log.info(f"  Already in DB   : {len(existing):,}")
    log.info(f"  Missing hours   : {len(missing):,}")
    log.info(f"  Rows with nulls : {len(with_nulls):,}")

    hours_to_fetch = missing | with_nulls
    if not hours_to_fetch:
        log.info("  All data complete — nothing to do")
        conn.close()
        return

    # PASO 2 — Chunks
    chunks = missing_to_chunks(hours_to_fetch)
    log.info(f"Step 2 — Chunks to fetch: {len(chunks)}")

    # PASO 3 — Fetch + Insert + Update
    ins, upd, errors = 0, 0, 0
    t0 = time.time()

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        log.info(f"Step 3 [{i}/{len(chunks)}] Fetching {chunk_start} → {chunk_end}")
        df = fetch_chunk(headers, chunk_start, chunk_end)

        if df is not None and not df.empty:
            try:
                n_ins = insert_rows(conn, df, missing)
                n_upd = update_null_rows(conn, df, with_nulls)
                ins  += n_ins
                upd  += n_upd
                if n_ins > 0: log.info(f"  Inserted {n_ins} new rows")
                if n_upd > 0: log.info(f"  Updated  {n_upd} rows (nulls filled)")
            except Exception as e:
                log.error(f"  Error: {e}")
                conn.rollback()
                errors += 1
        else:
            log.warning(f"  No data for {chunk_start} → {chunk_end}")
            errors += 1

    duration = time.time() - t0
    status   = "ok" if errors == 0 else ("partial" if (ins+upd) > 0 else "error")
    message  = f"{ins} inserted, {upd} updated, {errors} errors, {duration:.0f}s"
    log_pipeline(conn, start_date, end_date, ins + upd, status, message, duration)
    conn.close()

    log.info("=" * 60)
    log.info(f"  DONE: {ins} inserted | {upd} updated | {errors} errors | {duration:.1f}s")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
