"""
TFM Energia UCM — Commodities Daily Pipeline
Actualiza diariamente los precios de commodities energeticas desde Yahoo Finance.

Fuentes:
  - TTF=F  : Gas natural TTF (Dutch Title Transfer) EUR/MWh
  - CO2.L  : CO2 ETS European Allowances EUR/t
  - MTF=F  : Carbon API2 futures USD/t

Logica:
  - Descarga precio de cierre del dia anterior
  - Revision ultimos 7 dias para rellenar huecos
  - Yahoo Finance no publica fines de semana ni festivos — null es valido esos dias
  - Reintentos si falla la conexion con Yahoo Finance
  - Registro en pipeline_log

Cron job (servidor):
    0 18 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/commodities_pipeline.py >> /home/ubuntu/scripts/logs/cron_commodities.log 2>&1

Usage:
    python commodities_pipeline.py              # carga ayer + revision 7 dias
    python commodities_pipeline.py --fecha 2026-07-15  # fecha concreta
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_REINTENTOS  = 3
PAUSA_REINTENTO = 30   # segundos entre reintentos Yahoo Finance
DIAS_REVISION   = 7

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TICKERS = {
    "TTF=F":  "gas_ttf",   # Gas TTF EUR/MWh
    "CO2.L":  "co2_ets",   # CO2 ETS EUR/t
    # "MTF=F": "carbon_api2" — producto retirado de Yahoo Finance jul-2026
}

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(run_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"commodities_pipeline_{run_date}.log"
    logger = logging.getLogger(f"commodities_{run_date}")
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

# ── Yahoo Finance ──────────────────────────────────────────────────────────────

def download_ticker(ticker: str, start: date, end: date, log) -> pd.Series | None:
    """
    Descarga precio de cierre diario de Yahoo Finance con reintentos.
    Yahoo Finance no devuelve datos para fines de semana ni festivos — es correcto.
    """
    start_str = str(start)
    # Yahoo Finance end es exclusivo — sumar 1 dia
    end_str   = str(end + timedelta(days=1))

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            df = yf.download(ticker, start=start_str, end=end_str,
                           progress=False, auto_adjust=True)
            if df.empty:
                log.warning(f"  {ticker}: sin datos para {start} → {end}")
                return None

            if isinstance(df.columns, pd.MultiIndex):
                close = df["Close"][ticker]
            else:
                close = df["Close"]

            close = close.dropna()
            if not close.empty:
                log.info(f"  {ticker}: {len(close)} dias "
                        f"({close.index[0].date()} → {close.index[-1].date()})")
            return close

        except Exception as e:
            log.warning(f"  {ticker} intento {intento}/{MAX_REINTENTOS}: {e}")
            if intento < MAX_REINTENTOS:
                time.sleep(PAUSA_REINTENTO)

    log.error(f"  {ticker}: fallido tras {MAX_REINTENTOS} intentos")
    return None

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_existing_dates(conn, start: date, end: date) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT fecha FROM commodities WHERE fecha >= %s AND fecha <= %s",
                   (start, end))
        return {row[0] for row in cur.fetchall()}


def get_dates_with_nulls(conn, col: str, start: date, end: date) -> set:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT fecha FROM commodities
            WHERE fecha >= %s AND fecha <= %s AND {col} IS NULL
        """, (start, end))
        return {row[0] for row in cur.fetchall()}


def insert_rows(conn, records: list, col: str) -> int:
    if not records:
        return 0
    sql = f"INSERT INTO commodities (fecha, {col}) VALUES %s ON CONFLICT (fecha) DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def update_nulls(conn, records: list, col: str) -> int:
    if not records:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for fecha, valor in records:
            cur.execute(f"""
                UPDATE commodities SET {col} = %s
                WHERE fecha = %s AND {col} IS NULL
            """, (valor, fecha))
            if cur.rowcount > 0:
                updated += 1
    conn.commit()
    return updated


def log_pipeline_db(conn, start, end, ins, upd, status, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, ("commodities_daily", start, end, ins + upd,
                  status, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()

# ── Carga de un rango de fechas ────────────────────────────────────────────────

def cargar_rango(start: date, end: date, db_config: dict, log) -> tuple[int, int]:
    """Carga todos los tickers para un rango de fechas. Retorna (insertadas, actualizadas)."""
    t0 = time.time()
    conn = psycopg2.connect(**db_config)
    existing = get_existing_dates(conn, start, end)
    total_ins = total_upd = 0

    for ticker, col in TICKERS.items():
        with_nulls = get_dates_with_nulls(conn, col, start, end)
        serie = download_ticker(ticker, start, end, log)
        if serie is None:
            continue

        new_records    = []
        update_records = []

        for dt, valor in serie.items():
            fecha = dt.date() if hasattr(dt, 'date') else dt
            if pd.isna(valor):
                continue
            if fecha not in existing:
                new_records.append((fecha, round(float(valor), 4)))
            elif fecha in with_nulls:
                update_records.append((fecha, round(float(valor), 4)))

        if new_records:
            n = insert_rows(conn, new_records, col)
            total_ins += n
            log.info(f"  {ticker}: INSERT {n} filas")
        if update_records:
            n = update_nulls(conn, update_records, col)
            total_upd += n
            log.info(f"  {ticker}: UPDATE {n} filas")
        if not new_records and not update_records:
            log.info(f"  {ticker}: sin cambios")

        time.sleep(0.5)

    duracion = time.time() - t0
    estado   = "ok" if (total_ins + total_upd) >= 0 else "error"
    mensaje  = f"{total_ins} insert, {total_upd} update | {start} → {end}"
    log_pipeline_db(conn, start, end, total_ins, total_upd, estado, mensaje, duracion, log)
    conn.close()
    return total_ins, total_upd

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    hoy   = date.today()
    ayer  = hoy - timedelta(days=1)
    log   = setup_logger(hoy)

    log.info("=" * 55)
    log.info(f"Commodities Pipeline — {hoy}")
    log.info(f"Tickers: {list(TICKERS.keys())}")
    log.info(f"Revision ultimos {DIAS_REVISION} dias")
    log.info("=" * 55)

    _, db_config = load_config()

    # PASO 1 — Cargar ayer
    log.info(f"\n=== PASO 1: Dia principal — {ayer} ===")
    ins, upd = cargar_rango(ayer, ayer, db_config, log)
    log.info(f"  Resultado: {ins} insert, {upd} update")

    # PASO 2 — Revision ultimos 7 dias
    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    start_rev = hoy - timedelta(days=DIAS_REVISION)
    ins2, upd2 = cargar_rango(start_rev, ayer, db_config, log)
    log.info(f"  Revision: {ins2} insert, {upd2} update")

    log.info("\nPipeline Commodities finalizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Commodities daily pipeline")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    args = parser.parse_args()

    if args.fecha:
        _, db_config = load_config()
        target = date.fromisoformat(args.fecha)
        log    = setup_logger(target)
        log.info(f"Modo manual — {target}")
        ins, upd = cargar_rango(target, target, db_config, log)
        log.info(f"Resultado: {ins} insert, {upd} update")
    else:
        run()
