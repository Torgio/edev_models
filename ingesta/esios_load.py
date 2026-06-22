"""
TFM Energia UCM — ESIOS Flexible Data Loader
Antes de descargar de la API consulta la BD y calcula exactamente
que horas faltan. Solo descarga e inserta lo que no existe ya.

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

import json
import time
import logging
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from calendar import monthrange

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI             ║
# ╚══════════════════════════════════════════════════════════════╝

MODE  = "test"      # "test" | "week" | "month" | "year" | "range"

YEAR  = 2020        # Año  (usado en: year, month)
MONTH = 1           # Mes  (usado en: month, 1-12)

WEEK_START = "2020-01-06"   # Lunes de la semana (usado en: week)

START_DATE = "2020-01-01"   # Inicio rango libre (usado en: range / test)
END_DATE   = "2020-01-31"   # Fin rango libre    (usado en: range)

# ╔══════════════════════════════════════════════════════════════╝
# ║                  FIN DE CONFIGURACION                       ║
# ╚══════════════════════════════════════════════════════════════╝

DB_CONFIG = {
    "host":     "91.134.143.153",
    "port":     5432,
    "dbname":   "tfm_energia",
    "user":     "postgres",
    "password": "TFMenergia2026#",
}

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"

INDICATORS = {
    "price_eur_mwh":         600,
    "demanda_real_mw":      1293,
    "demanda_prev_mw":       544,
    "gen_solar_mw":          552,
    "gen_wind_mw":           551,
    "gen_hidro_real_mw":     546,
    "gen_nuclear_real_mw":   549,
    "gen_ciclocomb_real_mw": 550,
    "gen_coal_real_mw":      547,
    "gen_cogen_real_mw":     553,
    "resto_gen_real_mw":     555,
    "saldo_francia_mw":    10045,
    "saldo_portugal_mw":     557,
    "saldo_marruecos_mw":  10046,
    "gen_solar_prev_mw":     542,
    "gen_solar_term_prev_mw":543,
    "precio_banda_sec_mwh":  634,
    "gen_bombeo_turb_mw":   1152,
    "cons_bombeo_mw":       1172,
    "ntc_francia_imp_mw":   1844,
    "ntc_francia_exp_mw":   1848,
    "ntc_portugal_imp_mw":  1845,
    "gen_libre_co2_mw":    10006,
    "pct_gen_libre_co2":   10033,
    "precio_co2_despacho":  1391,
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

# ── BD check — que horas existen ya ───────────────────────────────────────────

def get_existing_hours(conn, start: date, end: date) -> set:
    """
    Consulta la BD y devuelve el conjunto de timestamps (UTC)
    que ya existen en marketdata_qh para el rango dado.
    Esta consulta se hace UNA VEZ antes de tocar la API.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT time_qh
            FROM marketdata_qh
            WHERE time_qh >= %s
              AND time_qh <  %s
        """, (
            datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            datetime(end.year,   end.month,   end.day,   tzinfo=timezone.utc) + timedelta(days=1),
        ))
        return {row[0] for row in cur.fetchall()}


def build_expected_hours(start: date, end: date) -> set:
    """
    Genera el conjunto completo de timestamps horarios esperados
    entre start y end (inclusive), en UTC.
    """
    hours = set()
    current = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt  = datetime(end.year,   end.month,   end.day,   23, tzinfo=timezone.utc)
    while current <= end_dt:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def get_missing_hours(existing: set, expected: set) -> set:
    return expected - existing


def missing_to_chunks(missing_hours: set, chunk_days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """
    Agrupa las horas faltantes en chunks de chunk_days dias
    para minimizar el numero de llamadas a la API.
    """
    if not missing_hours:
        return []
    days_missing = sorted({h.date() for h in missing_hours})
    chunks = []
    chunk_start = days_missing[0]
    prev_day    = days_missing[0]

    for day in days_missing[1:]:
        if (day - prev_day).days > 1 or (day - chunk_start).days >= chunk_days:
            chunks.append((chunk_start, prev_day))
            chunk_start = day
        prev_day = day
    chunks.append((chunk_start, prev_day))
    return chunks

# ── Credentials ────────────────────────────────────────────────────────────────

def load_headers() -> dict:
    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)
    return {
        "Host":         creds["Host"],
        "x-api-key":    creds["x-api-key"],
        "Accept":       "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
    }

# ── API ────────────────────────────────────────────────────────────────────────

def fetch_indicator(headers, indicator_id: int, start: date, end: date) -> pd.Series | None:
    url    = f"{ESIOS_BASE}/indicators/{indicator_id}"
    params = {
        "start_date": f"{start}T00:00:00",
        "end_date":   f"{end}T23:59:59",
        "time_trunc": "hour",
        "geo_ids[]":  3,
    }
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
    except requests.exceptions.HTTPError as e:
        log.warning(f"    HTTP {e.response.status_code} — indicator {indicator_id}")
        return None
    except Exception as e:
        log.error(f"    Error indicator {indicator_id}: {e}")
        return None


def fetch_chunk(headers, start: date, end: date) -> pd.DataFrame | None:
    frames = {}
    for col, ind_id in INDICATORS.items():
        serie = fetch_indicator(headers, ind_id, start, end)
        if serie is not None:
            frames[col] = serie
        time.sleep(PAUSE_SEC)
    if not frames:
        return None
    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()

# ── Insert ─────────────────────────────────────────────────────────────────────

ALL_COLS = ["time_qh"] + list(INDICATORS.keys())

def insert_rows(conn, df: pd.DataFrame, missing_hours: set) -> int:
    """
    Inserta solo las filas cuyo timestamp esta en missing_hours.
    Doble seguridad: ON CONFLICT DO NOTHING en SQL tambien lo protege.
    """
    df_filtered = df[df["time_qh"].isin(missing_hours)]
    if df_filtered.empty:
        return 0

    cols = [c for c in ALL_COLS if c in df_filtered.columns]
    records = [
        tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
        for _, row in df_filtered.iterrows()
    ]
    sql = f"""
        INSERT INTO marketdata_qh ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (time_qh) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=BATCH_SIZE)
    conn.commit()
    return len(records)


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
    headers = load_headers()

    log.info("=" * 60)
    log.info(f"  MODE   : {MODE}")
    log.info(f"  Period : {start_date} → {end_date}")
    log.info("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    log.info("Connected to PostgreSQL OK")

    # ── PASO 1: Consulta BD — qué horas ya existen ──────────────
    log.info("Checking existing data in DB...")
    existing  = get_existing_hours(conn, start_date, end_date)
    expected  = build_expected_hours(start_date, end_date)
    missing   = get_missing_hours(existing, expected)

    log.info(f"  Expected hours : {len(expected):,}")
    log.info(f"  Already in DB  : {len(existing):,}")
    log.info(f"  Missing hours  : {len(missing):,}")

    if not missing:
        log.info("  Nothing to load — all data already in DB")
        conn.close()
        return

    # ── PASO 2: Agrupar horas faltantes en chunks ────────────────
    chunks = missing_to_chunks(missing)
    log.info(f"  Chunks to fetch: {len(chunks)}")

    # ── PASO 3: Descargar solo los chunks que faltan ─────────────
    total, errors = 0, 0
    t0 = time.time()

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        log.info(f"  [{i}/{len(chunks)}] Fetching {chunk_start} → {chunk_end}")
        df = fetch_chunk(headers, chunk_start, chunk_end)

        if df is not None and not df.empty:
            try:
                # Solo inserta timestamps que realmente faltan
                n = insert_rows(conn, df, missing)
                total += n
                log.info(f"    Inserted {n} rows")
            except Exception as e:
                log.error(f"    Insert error: {e}")
                conn.rollback()
                errors += 1
        else:
            log.warning(f"    No data for {chunk_start} → {chunk_end}")
            errors += 1

    duration = time.time() - t0
    status   = "ok" if errors == 0 else ("partial" if total > 0 else "error")
    message  = f"{total} rows inserted, {errors} errors, {duration:.0f}s"
    log_pipeline(conn, start_date, end_date, total, status, message, duration)
    conn.close()

    log.info("=" * 60)
    log.info(f"  DONE: {total} rows | {errors} errors | {duration:.1f}s | {status}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
