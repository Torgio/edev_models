"""
TFM Energia UCM — ESIOS Daily Update
Descarga automaticamente los datos del dia anterior y los carga en BD.
Disenado para ejecutarse cada dia a las 09:00 via cron job.

Cron job (en el servidor):
    0 9 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_daily.py >> /home/ubuntu/scripts/logs/daily.log 2>&1

Usage:
    python esios_daily.py              # carga datos de ayer
    python esios_daily.py --days 3     # carga ultimos 3 dias (recuperacion)
"""

import json
import time
import logging
import argparse
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ── Configuration ──────────────────────────────────────────────────────────────

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
PAUSE_SEC  = 0.5
BATCH_SIZE = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("esios_daily")

# ── Helpers ────────────────────────────────────────────────────────────────────

def load_headers() -> dict:
    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)
    return {
        "Host":         creds["Host"],
        "x-api-key":    creds["x-api-key"],
        "Accept":       "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
    }


def day_already_loaded(conn, target: date) -> bool:
    """Check if target date already has data in DB."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marketdata_qh
            WHERE time_qh::date = %s
        """, (target,))
        return cur.fetchone()[0] >= 20  # at least 20 hours = day is loaded


def fetch_indicator(headers, indicator_id: int, target: date) -> pd.Series | None:
    url = f"{ESIOS_BASE}/indicators/{indicator_id}"
    params = {
        "start_date": f"{target}T00:00:00",
        "end_date":   f"{target}T23:59:59",
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
    except Exception as e:
        log.error(f"  Error indicator {indicator_id}: {e}")
        return None


def fetch_day(headers, target: date) -> pd.DataFrame | None:
    frames = {}
    for col, ind_id in INDICATORS.items():
        serie = fetch_indicator(headers, ind_id, target)
        if serie is not None:
            frames[col] = serie
        time.sleep(PAUSE_SEC)

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()


ALL_COLS = ["time_qh"] + list(INDICATORS.keys())

def insert_rows(conn, df: pd.DataFrame) -> int:
    cols = [c for c in ALL_COLS if c in df.columns]
    records = [
        tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
        for _, row in df.iterrows()
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


def log_pipeline(conn, target, n, status, message, duration):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, ("esios_daily", target, target, n, status, message, round(duration, 2)))
        conn.commit()
    except Exception:
        conn.rollback()


# ── Main ───────────────────────────────────────────────────────────────────────

def run(days_back: int = 1):
    headers = load_headers()
    conn    = psycopg2.connect(**DB_CONFIG)
    log.info("Connected to PostgreSQL OK")

    total, errors = 0, 0
    t0 = time.time()

    for i in range(days_back, 0, -1):
        target = date.today() - timedelta(days=i)
        log.info(f"Processing {target}...")

        if day_already_loaded(conn, target):
            log.info(f"  SKIP — {target} already in DB")
            continue

        df = fetch_day(headers, target)
        if df is not None and not df.empty:
            try:
                n = insert_rows(conn, df)
                total += n
                log.info(f"  OK — {n} rows inserted")
            except Exception as e:
                log.error(f"  Insert error: {e}")
                conn.rollback()
                errors += 1
        else:
            log.warning(f"  No data for {target}")
            errors += 1

        duration = time.time() - t0
        status   = "ok" if errors == 0 else ("partial" if total > 0 else "error")
        log_pipeline(conn, target, total, status, f"{total} rows, {errors} errors", duration)

    conn.close()
    log.info(f"DONE: {total} rows | {errors} errors | {time.time()-t0:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESIOS daily update")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of past days to load (default: 1 = yesterday)")
    args = parser.parse_args()
    run(days_back=args.days)
