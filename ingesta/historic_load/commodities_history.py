"""
TFM Energia UCM — Commodities Data Loader (Yahoo Finance)
=========================================================
Descarga precios diarios de commodities energeticas desde Yahoo Finance y los
carga en la tabla commodities de PostgreSQL.

FUENTES ACTIVAS
  - TTF=F  : Gas natural TTF (Dutch Title Transfer Facility) EUR/MWh, desde 2020
             Verificado 12/08/2026: el ticker SIGUE VIVO y cotizando en Yahoo.
  - MTF=F  : Carbon API 2 (carbon Rotterdam) USD/t, desde 2020

FUENTE RETIRADA — CO2.L
  El ticker de CO2 ETS de Yahoo dejo de actualizarse. Ademas solo cubria desde
  oct-2021, asi que 2020 y buena parte de 2021 estaban vacios de todas formas.
  El CO2 pasa a cargarse desde ESIOS con el indicador 1391 (PCO2D), que cubre
  2020-2026 completo, es fuente oficial y publica, y sigue viva:
      ingesta/historic_load/commodities_co2_esios.py

  MOTIVO DE HABERLO QUITADO DE AQUI, no solo de comentarlo: si un ticker
  falla dentro del bucle puede dejar la sesion de yfinance en mal estado y
  arrastrar a los siguientes. El corte simultaneo de las tres columnas a
  finales de julio de 2026 es coherente con ese patron.

LOGICA ANTI-DUPLICADOS
  - Consulta la BD antes de descargar, para no pedir lo que ya esta
  - INSERT solo de fechas nuevas
  - UPDATE solo de columnas NULL (con --sobrescribir se pisan tambien las que
    ya tengan valor, util para corregir una carga defectuosa)
  - ON CONFLICT DO NOTHING como doble proteccion

NOTA SOBRE LOS HUECOS: TTF=F y MTF=F son mercados financieros y NO cotizan
sabados, domingos ni festivos. Los NULL de esos dias no son un fallo de carga.
gas_mibgas si tiene todos los dias porque el gas se entrega a diario.

USO
    python commodities_history.py                            # 2020 -> hoy
    python commodities_history.py --start 2026-07-28         # desde una fecha
    python commodities_history.py --yesterday                # solo ayer
    python commodities_history.py --start ... --sobrescribir # pisar valores
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

# Tickers Yahoo Finance → columna BD
TICKERS = {
    "TTF=F":  "gas_ttf",     # Gas TTF EUR/MWh   — desde 2020-01-02, ACTIVO
    "MTF=F":  "carbon_api2", # Carbon API2 USD/t — desde 2020-01-02, ACTIVO
    # "CO2.L": "co2_ets"     — RETIRADO: dejo de actualizarse y solo cubria
    #                          desde oct-2021. El CO2 se carga desde ESIOS
    #                          (indicador 1391) con commodities_co2_esios.py
}

# Antes estaban fijas en un rango de una carga puntual (24-jun a 16-jul de
# 2026), asi que ejecutar el script sin argumentos solo cargaba tres semanas.
START_DATE_DEFAULT = "2026-07-01"
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


def update_rows(conn, records: list, col: str, sobrescribir: bool = False) -> int:
    """
    Rellena la columna en filas existentes.
    Por defecto solo donde esta NULL; con sobrescribir=True pisa tambien los
    valores que ya tengan dato, util para corregir una carga defectuosa.

    En lote: 1 UPDATE en vez de uno por fila. Con la latencia del servidor,
    la version por filas tardaba minutos en rangos largos.
    """
    if not records:
        return 0
    filtro = "" if sobrescribir else f" AND c.{col} IS NULL"
    with conn.cursor() as cur:
        execute_values(cur, f"""
            UPDATE commodities AS c
            SET {col} = v.valor
            FROM (VALUES %s) AS v(f, valor)
            WHERE c.fecha = v.f::date{filtro}
        """, records, template="(%s, %s::numeric)", page_size=500)
        n = cur.rowcount
    conn.commit()
    return n

# ── Main ───────────────────────────────────────────────────────────────────────

def run(start: str, end: str, sobrescribir: bool = False):
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Conectado a PostgreSQL")
    log.info(f"Periodo: {start} -> {end}")
    log.info(f"Tickers: {list(TICKERS.keys())}")
    if sobrescribir:
        log.warning("MODO SOBRESCRIBIR: se pisaran los valores existentes")

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
            elif sobrescribir or fecha in with_nulls:
                update_records.append((fecha, float(valor)))

        log.info(f"  A insertar   : {len(new_records)}")
        log.info(f"  A actualizar : {len(update_records)}")
        log.info(f"  Ya completas : {len(serie) - len(new_records) - len(update_records)}")

        # INSERT filas nuevas
        if new_records:
            ins = insert_rows(conn, new_records, col)
            total_ins += ins
            log.info(f"  Insertadas {ins} filas")

        # UPDATE de filas existentes
        if update_records:
            upd = update_rows(conn, update_records, col, sobrescribir)
            total_upd += upd
            log.info(f"  Actualizadas {upd} filas")

        time.sleep(0.5)

    # Estado final por columna, para ver de un vistazo si queda algun hueco
    log.info("\n" + "=" * 70)
    log.info("ESTADO DE LA TABLA commodities POR AÑO")
    log.info("=" * 70)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT extract(year from fecha)::int AS anio,
                       count(*)            AS filas,
                       count(gas_ttf)      AS ttf,
                       count(co2_ets)      AS co2,
                       count(gas_mibgas)   AS mibgas,
                       count(carbon_api2)  AS api2,
                       max(fecha)          AS ultima
                FROM commodities GROUP BY 1 ORDER BY 1
            """)
            log.info(f"  {'anio':>6} {'filas':>6} {'ttf':>6} {'co2':>6} "
                     f"{'mibgas':>7} {'api2':>6}  ultima")
            for a, f, t, c, m, ap, u in cur.fetchall():
                log.info(f"  {a:>6} {f:>6} {t:>6} {c:>6} {m:>7} {ap:>6}  {u}")
    except Exception as e:
        log.warning(f"  No se pudo consultar el estado: {e}")

    conn.close()
    log.info("=" * 70)
    log.info(f"HECHO: {total_ins} insertadas | {total_upd} actualizadas")
    log.info("Recuerda: el CO2 se carga aparte con commodities_co2_esios.py "
             "(ESIOS 1391)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Commodities → PostgreSQL")
    parser.add_argument("--start",     default=START_DATE_DEFAULT)
    parser.add_argument("--end",       default=END_DATE_DEFAULT)
    parser.add_argument("--yesterday", action="store_true",
                        help="Carga solo el dia anterior")
    parser.add_argument("--sobrescribir", action="store_true",
                        help="Pisa los valores existentes en vez de rellenar "
                             "solo los NULL (para corregir cargas defectuosas)")
    args = parser.parse_args()

    if args.yesterday:
        d = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        run(d, d, args.sobrescribir)
    else:
        run(args.start, args.end, args.sobrescribir)