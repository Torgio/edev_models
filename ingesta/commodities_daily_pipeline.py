"""
TFM Energia UCM — Commodities Daily Pipeline
============================================
Actualiza diariamente los precios de commodities energeticas en la tabla
commodities, combinando DOS fuentes con granularidades distintas.

FUENTE 1 — Yahoo Finance (diaria)
  - TTF=F : Gas natural TTF (Dutch Title Transfer Facility) EUR/MWh. ACTIVO,
            verificado el 12/08/2026.
  Yahoo no publica fines de semana ni festivos, asi que los NULL de esos dias
  no son un fallo de carga.

FUENTE 2 — ESIOS, indicador 1391 (mensual)
  - co2_ets : precio de derechos de emision. Sustituye al ticker CO2.L de
              Yahoo, que dejo de actualizarse (y que ademas solo cubria desde
              oct-2021, dejando 2020 y parte de 2021 vacios).
  El 1391 es MENSUAL: el valor se replica en todos los dias del mes. Como
  feature capta la tendencia entre meses, no los movimientos diarios del
  mercado de emisiones. Se pide cada noche porque asi el cambio de mes se
  recoge solo, sin necesidad de un cron mensual aparte, y porque ESIOS puede
  revisar el valor del mes en curso.

TICKERS RETIRADOS Y POR QUE
  - CO2.L : dejo de actualizarse. Reemplazado por ESIOS 1391 (arriba).
  - MTF=F : delistado en Yahoo ("possibly delisted; no price data found").
            Ademas el carbon peninsular cerro en 2021 y la columna coal_mw de
            entsoe_gen_data esta vacia desde entonces, asi que el precio del
            carbon no explica el precio electrico español actual. La columna
            carbon_api2 se conserva congelada con el historico 2020-2025 como
            registro del periodo en que si habia generacion con carbon.

  Importa haberlos QUITADO del diccionario y no solo comentado su uso: un
  ticker que falla dentro del bucle puede dejar la sesion de yfinance en mal
  estado y arrastrar a los siguientes. El corte simultaneo de las tres
  columnas a finales de julio de 2026 encaja con ese patron.

LOGICA
  - Carga el cierre del dia anterior y revisa los ultimos 7 dias para rellenar
    huecos, con reintentos si Yahoo falla
  - El CO2 se recarga siempre para los 2 ultimos meses, SOBRESCRIBIENDO, para
    recoger revisiones de ESIOS
  - Registro en pipeline_log

CRON (servidor):
    0 18 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/commodities_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_commodities.log 2>&1

USO
    python commodities_daily_pipeline.py                     # ayer + revision
    python commodities_daily_pipeline.py --fecha 2026-07-15  # fecha concreta
    python commodities_daily_pipeline.py --solo-co2          # solo el CO2
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import requests
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
    "TTF=F":  "gas_ttf",   # Gas TTF EUR/MWh — ACTIVO
    # "CO2.L": "co2_ets"      — RETIRADO: dejo de actualizarse. El CO2 se carga
    #                           desde ESIOS 1391 (ver cargar_co2_esios)
    # "MTF=F": "carbon_api2"  — RETIRADO: delistado en Yahoo, y el carbon
    #                           peninsular cerro en 2021
}

# ── CO2 desde ESIOS (indicador mensual) ──
ESIOS_URL      = "https://api.esios.ree.es/indicators"
ESIOS_CO2_IND  = 1391          # "Precio de derechos de emision de despacho (PCO2D)"
CO2_COL        = "co2_ets"
CO2_MESES_ATRAS = 2            # se recargan los 2 ultimos meses por si ESIOS
                               # revisa el valor del mes en curso
CREDS_PATH = Path(__file__).parent / "credentials.json"

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

# ── CO2 desde ESIOS ────────────────────────────────────────────────────────────

def _esios_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


def descargar_co2_esios(desde: date, hasta: date, log) -> dict:
    """
    Descarga el indicador 1391 en su granularidad NATIVA (mensual).
    Devuelve {fecha_observacion: valor}.
    """
    url = (f"{ESIOS_URL}/{ESIOS_CO2_IND}"
           f"?start_date={desde}T00:00:00&end_date={hasta}T23:59:59")
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            r = requests.get(url, headers=_esios_headers(), timeout=60)
            r.raise_for_status()
            out = {}
            for v in r.json().get("indicator", {}).get("values", []):
                dt_str, val = (v.get("datetime") or v.get("datetime_utc")), v.get("value")
                if not dt_str or val is None:
                    continue
                try:
                    out[datetime.fromisoformat(dt_str.replace("Z", "+00:00")).date()] = float(val)
                except Exception:
                    pass
            return out
        except Exception as e:
            log.warning(f"  ESIOS {ESIOS_CO2_IND} intento {intento}/{MAX_REINTENTOS}: "
                        f"{str(e)[:60]}")
            if intento < MAX_REINTENTOS:
                time.sleep(PAUSA_REINTENTO)
    log.error(f"  ESIOS {ESIOS_CO2_IND}: fallido tras {MAX_REINTENTOS} intentos")
    return {}


def cargar_co2_esios(db_config: dict, log, meses_atras: int = CO2_MESES_ATRAS) -> int:
    """
    Carga el CO2 de los ultimos meses en commodities.co2_ets.

    El valor mensual se replica en TODOS los dias de su mes, para que la
    columna sea utilizable junto a las diarias sin necesidad de un JOIN.
    Se SOBRESCRIBE siempre: ESIOS puede revisar el valor del mes en curso, y
    ademas el ultimo mes se va completando conforme avanza.
    """
    hoy = date.today()
    # primer dia del mes N meses atras
    y, m = hoy.year, hoy.month - meses_atras
    while m <= 0:
        m += 12
        y -= 1
    desde = date(y, m, 1)

    log.info(f"  Indicador {ESIOS_CO2_IND} (mensual) desde {desde}")
    datos = descargar_co2_esios(desde, hoy, log)
    if not datos:
        return 0

    # Expandir cada observacion mensual a los dias de su mes
    fechas = sorted(datos)
    diario = {}
    for i, f in enumerate(fechas):
        fin = (fechas[i+1] - timedelta(days=1)) if i + 1 < len(fechas) else hoy
        d = f
        while d <= fin:
            diario[d] = datos[f]
            d += timedelta(days=1)

    log.info(f"  {len(datos)} observaciones -> {len(diario)} dias")

    conn = psycopg2.connect(**db_config)
    fechas_d = sorted(diario)
    existing = get_existing_dates(conn, fechas_d[0], fechas_d[-1])

    nuevas  = [(f, round(diario[f], 4)) for f in fechas_d if f not in existing]
    upserts = [(f, round(diario[f], 4)) for f in fechas_d if f in existing]

    ins = upd = 0
    if nuevas:
        ins = insert_rows(conn, nuevas, CO2_COL)
    if upserts:
        with conn.cursor() as cur:
            execute_values(cur, f"""
                UPDATE commodities AS c
                SET {CO2_COL} = v.valor
                FROM (VALUES %s) AS v(f, valor)
                WHERE c.fecha = v.f::date
            """, upserts, template="(%s, %s::numeric)", page_size=500)
            upd = cur.rowcount
        conn.commit()
    conn.close()

    log.info(f"  co2_ets: {ins} insert, {upd} update "
             f"(ultimo valor {datos[fechas[-1]]:.2f} EUR/t de {fechas[-1]})")
    return ins + upd


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

    log.info("=" * 62)
    log.info(f"Commodities Pipeline — {hoy}")
    log.info(f"Yahoo Finance: {list(TICKERS.keys())}")
    log.info(f"ESIOS: indicador {ESIOS_CO2_IND} (CO2, mensual) -> {CO2_COL}")
    log.info(f"Revision ultimos {DIAS_REVISION} dias")
    log.info("=" * 62)

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

    # PASO 3 — CO2 desde ESIOS (mensual)
    # Se hace cada noche aunque el dato sea mensual: asi el cambio de mes se
    # recoge solo y no hace falta un cron aparte. Es una sola peticion.
    log.info(f"\n=== PASO 3: CO2 desde ESIOS (indicador {ESIOS_CO2_IND}) ===")
    try:
        n_co2 = cargar_co2_esios(db_config, log)
        log.info(f"  Resultado: {n_co2} celdas escritas")
    except Exception as e:
        log.error(f"  Error cargando CO2: {e}")

    log.info("\nPipeline Commodities finalizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Commodities daily pipeline")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    parser.add_argument("--solo-co2", action="store_true",
                        help="Carga unicamente el CO2 desde ESIOS")
    args = parser.parse_args()

    if args.solo_co2:
        _, db_config = load_config()
        log = setup_logger(date.today())
        log.info(f"Modo solo CO2 — indicador {ESIOS_CO2_IND}")
        n = cargar_co2_esios(db_config, log)
        log.info(f"Resultado: {n} celdas escritas")
    elif args.fecha:
        _, db_config = load_config()
        target = date.fromisoformat(args.fecha)
        log    = setup_logger(target)
        log.info(f"Modo manual — {target}")
        ins, upd = cargar_rango(target, target, db_config, log)
        log.info(f"Resultado: {ins} insert, {upd} update")
    else:
        run()