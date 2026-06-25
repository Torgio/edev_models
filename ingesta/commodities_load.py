"""
TFM Energia UCM — Commodities Data Loader
Descarga precios diarios de commodities energeticas desde Yahoo Finance
y los carga en la tabla commodities de PostgreSQL.

Fuentes Yahoo Finance:
  - TTF=F  : Gas natural TTF (Dutch Title Transfer) €/MWh — desde 2020
  - CO2.L  : CO2 ETS European Allowances €/t         — desde oct-2021
  - MTF=F  : Carbon API2 futures $/t                 — desde 2020

Logica anti-duplicados:
  - Consulta BD antes de descargar
  - INSERT solo fechas nuevas
  - UPDATE solo columnas NULL en fechas existentes
  - ON CONFLICT DO NOTHING como doble proteccion

Usage:
    python commodities_load.py                     # historico completo
    python commodities_load.py --start 2024-01-01  # desde fecha concreta
    python commodities_load.py --yesterday          # solo ayer
"""

import argparse
import logging
import time
from datetime import date, timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

# Tickers Yahoo Finance → columna BD
TICKERS = {
    "TTF=F":  "gas_ttf",     # Gas TTF €/MWh    — desde 2020-01-02
    "CO2.L":  "co2_ets",     # CO2 ETS €/t      — desde 2021-10-18
    "MTF=F":  "carbon_api2", # Carbon API2 $/t  — desde 2020-01-02
}

START_DATE_DEFAULT = "2020-01-01"
END_DATE_DEFAULT   = date.today().strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("commodities_load")

# ── Descarga Yahoo Finance ─────────────────────────────────────────────────────

def download_ticker(ticker: str, start: str, end: str) -> pd.Series | None:
    """Descarga el precio de cierre diario de un ticker de Yahoo Finance."""
    try:
        df = yf.download(ticker, start=start, end=end, progress=False)
        if df.empty:
            log.warning(f"  {ticker}: sin datos para {start} → {end}")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"][ticker]
        else:
            close = df["Close"]

        close = close.dropna()
        log.info(f"  {ticker}: {len(close)} filas "
                 f"({close.index[0].date()} → {close.index[-1].date()})")
        return close

    except Exception as e:
        log.error(f"  Error descargando {ticker}: {e}")
        return None

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_existing_dates(conn, start: str, end: str) -> set:
    """Fechas que ya existen en la tabla commodities."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fecha FROM commodities
            WHERE fecha >= %s AND fecha <= %s
        """, (start, end))
        return {row[0] for row in cur.fetchall()}


def get_dates_with_nulls(conn, col: str, start: str, end: str) -> set:
    """Fechas existentes con NULL en una columna concreta."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT fecha FROM commodities
            WHERE fecha >= %s AND fecha <= %s
            AND {col} IS NULL
        """, (start, end))
        return {row[0] for row in cur.fetchall()}


def insert_rows(conn, records: list, col: str) -> int:
    """INSERT filas nuevas — ON CONFLICT DO NOTHING como doble proteccion."""
    if not records:
        return 0
    sql = f"""
        INSERT INTO commodities (fecha, {col})
        VALUES %s
        ON CONFLICT (fecha) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def update_nulls(conn, records: list, col: str) -> int:
    """UPDATE columna null en filas existentes — solo si el valor es nuevo."""
    if not records:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for fecha, valor in records:
            cur.execute(f"""
                UPDATE commodities
                SET {col} = %s
                WHERE fecha = %s AND {col} IS NULL
            """, (valor, fecha))
            if cur.rowcount > 0:
                updated += 1
    conn.commit()
    return updated

# ── Main ───────────────────────────────────────────────────────────────────────

def run(start: str, end: str):
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")
    log.info(f"Period: {start} → {end}")

    # Consulta UNICA a BD antes de tocar la API
    log.info("Checking existing data in DB...")
    existing = get_existing_dates(conn, start, end)
    log.info(f"  Dates already in DB: {len(existing)}")

    total_ins = 0
    total_upd = 0

    for ticker, col in TICKERS.items():
        log.info(f"\nProcessing {ticker} → column '{col}'")

        # Columnas con null para este indicador
        with_nulls = get_dates_with_nulls(conn, col, start, end)

        # Descargar datos de Yahoo Finance
        serie = download_ticker(ticker, start, end)
        if serie is None:
            continue

        # Clasificar registros: nuevos vs actualizar nulls
        new_records    = []
        update_records = []

        for dt, valor in serie.items():
            fecha = dt.date()
            if pd.isna(valor):
                continue
            if fecha not in existing:
                new_records.append((fecha, float(valor)))
            elif fecha in with_nulls:
                update_records.append((fecha, float(valor)))

        log.info(f"  New rows to INSERT : {len(new_records)}")
        log.info(f"  Rows to UPDATE     : {len(update_records)}")
        log.info(f"  Already complete   : {len(serie) - len(new_records) - len(update_records)}")

        # INSERT filas nuevas
        if new_records:
            ins = insert_rows(conn, new_records, col)
            total_ins += ins
            log.info(f"  Inserted {ins} rows")

        # UPDATE nulls
        if update_records:
            upd = update_nulls(conn, update_records, col)
            total_upd += upd
            log.info(f"  Updated {upd} rows")

        time.sleep(0.5)

    conn.close()
    log.info(f"\nDONE: {total_ins} inserted | {total_upd} updated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Commodities → PostgreSQL")
    parser.add_argument("--start",     default=START_DATE_DEFAULT)
    parser.add_argument("--end",       default=END_DATE_DEFAULT)
    parser.add_argument("--yesterday", action="store_true",
                        help="Carga solo el dia anterior")
    args = parser.parse_args()

    if args.yesterday:
        d = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        run(d, d)
    else:
        run(args.start, args.end)
