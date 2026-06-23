"""
TFM Energia UCM — ESIOS Daily Update
Descarga automaticamente los datos del dia anterior.
Cron job: 0 9 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_daily.py

Usage:
    python esios_daily.py              # carga ayer
    python esios_daily.py --days 3     # carga ultimos 3 dias
"""

import time
import logging
import argparse
from datetime import date, timedelta, datetime, timezone

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from config import load_config

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
PAUSE_SEC  = 0.5
BATCH_SIZE = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("esios_daily")


def day_already_complete(conn, target: date) -> bool:
    """Verifica si el dia ya tiene datos completos (sin nulls en columnas clave)."""
    data_cols  = list(INDICATORS.keys())
    null_check = " OR ".join([f"{c} IS NULL" for c in data_cols])
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM marketdata_qh
            WHERE time_qh::date = %s AND NOT ({null_check})
        """, (target,))
        complete = cur.fetchone()[0]
    return complete >= 20


def fetch_day(headers, target: date) -> pd.DataFrame | None:
    frames = {}
    for col, (ind_id, geo_id) in INDICATORS.items():
        url    = f"{ESIOS_BASE}/indicators/{ind_id}"
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
            if values:
                df = pd.DataFrame(values)[["datetime_utc", "value"]]
                df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
                frames[col] = df.set_index("datetime_utc")["value"]
        except Exception as e:
            log.warning(f"  Error indicator {ind_id}: {e}")
        time.sleep(PAUSE_SEC)

    if not frames:
        return None
    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()


ALL_COLS = ["time_qh"] + list(INDICATORS.keys())

def upsert_day(conn, df: pd.DataFrame) -> tuple[int, int]:
    """INSERT filas nuevas + UPDATE nulls en filas existentes."""
    ins, upd = 0, 0
    data_cols = list(INDICATORS.keys())

    # INSERT nuevas
    cols = [c for c in ALL_COLS if c in df.columns]
    records = [
        tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
        for _, row in df.iterrows()
    ]
    sql = f"INSERT INTO marketdata_qh ({', '.join(cols)}) VALUES %s ON CONFLICT (time_qh) DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=BATCH_SIZE)
        ins = cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()

    # UPDATE nulls
    cols_str = ", ".join([c for c in data_cols if c in df.columns])
    with conn.cursor() as cur:
        for _, row in df.iterrows():
            ts = row["time_qh"]
            cur.execute(f"SELECT {cols_str} FROM marketdata_qh WHERE time_qh = %s", (ts,))
            db_row = cur.fetchone()
            if not db_row:
                continue
            cols_list = [c for c in data_cols if c in row.index]
            to_update = {col: row[col] for i, col in enumerate(cols_list)
                        if db_row[i] is None and not pd.isna(row.get(col))}
            if to_update:
                set_clause = ", ".join([f"{c} = %s" for c in to_update])
                cur.execute(f"UPDATE marketdata_qh SET {set_clause} WHERE time_qh = %s",
                           list(to_update.values()) + [ts])
                upd += 1
    conn.commit()
    return ins, upd


def run(days_back: int = 1):
    headers, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")

    total_ins, total_upd, errors = 0, 0, 0
    t0 = time.time()

    for i in range(days_back, 0, -1):
        target = date.today() - timedelta(days=i)
        log.info(f"Processing {target}...")

        if day_already_complete(conn, target):
            log.info(f"  SKIP — {target} already complete in DB")
            continue

        df = fetch_day(headers, target)
        if df is not None and not df.empty:
            try:
                ins, upd = upsert_day(conn, df)
                total_ins += ins
                total_upd += upd
                log.info(f"  OK — {ins} inserted, {upd} updated")
            except Exception as e:
                log.error(f"  Error: {e}")
                conn.rollback()
                errors += 1
        else:
            log.warning(f"  No data for {target}")
            errors += 1

    conn.close()
    log.info(f"DONE: {total_ins} inserted | {total_upd} updated | {errors} errors | {time.time()-t0:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    run(days_back=args.days)
