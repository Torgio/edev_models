"""
TFM Energia UCM — ESIOS Generación Daily Pipeline
=================================================
Carga diaria de la generacion peninsular medida en TIEMPO REAL por REE en la
tabla esios_gen. 16 indicadores, todos con geo_id=8741 y granularidad nativa
de 5 minutos.

POR QUE ESTA TABLA EXISTE SI YA ESTA entsoe_gen_data
Las dos fuentes se complementan, no se duplican, y cada una desglosa lo que la
otra agrupa:

  ESIOS aporta la SOLAR SEPARADA (1295 fotovoltaica / 1294 termosolar).
  ENTSO-E las agrupa en el codigo PSR B16 y no hay forma de separarlas: la
  documentacion oficial define B16 simplemente como "Solar". Verificado que la
  diferencia importa: de madrugada ENTSO-E marca ~650 MW mientras ESIOS marca
  ~15 MW, y esa diferencia es la termosolar con almacenamiento termico
  generando de noche.

  ENTSO-E aporta la HIDRAULICA SEPARADA (B12 embalse / B11 fluyente) y la
  BIOMASA frente a RESIDUOS. ESIOS solo publica esos desgloses con retraso M+1
  y POR PROVINCIAS, sin geo_id peninsular: verificado que el 1150 tiene 41 geos
  y el 1151 tiene 45, ninguna es 8741. Por eso aqui la hidraulica va agregada.

AGREGACION — time_agg=average
Los 16 indicadores son POTENCIA (MW) con 288 valores nativos al dia, asi que
el valor horario es el promedio de las 12 muestras de 5 minutos. Verificado en
el TEST 504 sobre el 1295: la media manual de las 12 muestras coincide al
centimo con lo que devuelve time_agg=average.
OJO: es lo CONTRARIO que esios_pbf_*, donde los programas son MWh
cuarto-horarios y usan time_agg=sum. Y sin especificar time_agg, ESIOS agrega
por SUMA por defecto, lo que en su dia inflo esios_marketdata x11-12 y
esios_forecast_da x4.

DOS PARTICULARIDADES QUE HAY QUE CONOCER
  1. ree_ghidro_mw (546) viene NETEADA de bombeo y llega a -3.299 MW a
     mediodia. Las Unidades de Gestion Hidraulica se miden en barras y ahi el
     consumo de bombeo de las centrales reversibles se resta de su propia
     generacion. Consecuencia: NO es sumable con ree_cpumping_mw, hay doble
     conteo. Confirmado que el 546 es medida real y no programa P48, tanto por
     la API como por el visor.
  2. ree_cpumping_mw y ree_cbattery_mw vienen NEGATIVOS desde ESIOS, mientras
     en entsoe_gen_data los consumos son positivos. Se guardan tal cual llegan;
     el signo hay que tenerlo presente al comparar o al construir balances.

ESPORADICOS — verificado con datos, no supuesto
  bateria y bombeo: sin datos hasta 2025 (comprobado en 2020, 2022 y 2024)
  coal:  cerrado desde 2021, devuelve 0.00 exacto
  oil:   residual, vacio en toda la serie probada
Sin esto el pipeline los reintentaria cada noche buscando datos que no existen.

CRON (servidor):
    0 19 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_gen_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_esios_gen.log 2>&1

USO
    python esios_gen_daily_pipeline.py                  # ayer + revision
    python esios_gen_daily_pipeline.py --fecha 2026-08-12
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import psycopg2
from psycopg2.extras import execute_values

from config import load_config

# ── Configuracion ─────────────────────────────────────────────────────────────

BASE_URL      = "https://api.esios.ree.es/indicators"
TABLA         = "esios_gen"
GEO_PENINSULA = 8741
TIME_AGG      = "average"          # MW: promedio. NO sum (ver docstring)

DIAS_REVISION = 7
PAUSA_SEC     = 0.3
TIMEOUT_SEC   = 60
MAX_REINTENTOS = 3

TZ_SPAIN = ZoneInfo("Europe/Madrid")
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
CREDS_PATH = Path(__file__).parent / "credentials.json"


# ── Indicadores, en el orden por familias de entsoe_gen_data ──────────────────

INDICADORES = {
    # Renovables no gestionables
    "ree_gsolar_mw":        1295,   # FV pura      (ENTSO-E: dentro de B16)
    "ree_gsolter_mw":       1294,   # termosolar   (ENTSO-E: dentro de B16)
    "ree_gwind_mw":          551,   #              (ENTSO-E: wind_mw)
    # Hidraulica agregada; el desglose embalse/fluyente va en entsoe_gen_data
    "ree_ghidro_mw":         546,   # NETEADA de bombeo, puede ser negativa
    # Almacenamiento
    "ree_gpumping_mw":      2079,   #              (ENTSO-E: pumping_gen_mw)
    "ree_cpumping_mw":      2078,   # NEGATIVO     (ENTSO-E: pumping_cons_mw)
    "ree_gbattery_mw":      2167,   # desde 2025   (ENTSO-E: battery_gen_mw)
    "ree_cbattery_mw":      2166,   # desde 2025, NEGATIVO
    # Renovables termicas
    "ree_gtermicarenew_mw": 1296,   # biogas+biomasa+oceano+geotermica
    # Agregados oficiales de REE
    "ree_grenew_mw":       10351,
    "ree_gnorenew_mw":     10352,
    # Termicas convencionales
    "ree_gnuclear_mw":       549,
    "ree_gccgas_mw":         550,   # solo ciclo combinado, SIN cogeneracion
    "ree_gcoal_mw":          547,   # cerrado desde 2021
    "ree_goil_mw":           548,   # fuel-gas, residual
    "ree_gotherthermal_mw": 1297,   # cogeneracion y resto
}
# ree_gtotalthermal_mw es GENERATED: la calcula PostgreSQL, no se escribe aqui.

# Columnas cuya ausencia NO debe marcar el dia como incompleto
ESPORADICOS = {
    "ree_gbattery_mw", "ree_cbattery_mw",   # sin datos antes de 2025
    "ree_gpumping_mw", "ree_cpumping_mw",   # sin datos antes de 2025
    "ree_gcoal_mw",                          # carbon cerrado en 2021
    "ree_goil_mw",                           # fuel-gas residual
}

# Si falta alguna de estas, el dia si esta incompleto de verdad
CRITICOS = {"ree_gsolar_mw", "ree_gwind_mw", "ree_gnuclear_mw", "ree_grenew_mw"}


def setup_logger(run_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"esios_gen_{run_date}.log"
    logger = logging.getLogger(f"esios_gen_{run_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


# ── Horas esperadas (soporta dias de 23h y 25h) ───────────────────────────────

def expected_hours_utc(dia: date) -> set:
    """
    Horas UTC que componen el dia ESPAÑOL. En los cambios de hora el dia tiene
    23 o 25 horas, asi que no vale con generar 24 timestamps fijos: en marzo el
    pipeline reintentaria eternamente y en octubre daria el dia por completo
    con una hora de menos.
    """
    h0  = datetime(dia.year, dia.month, dia.day, 0, tzinfo=TZ_SPAIN)
    h23 = datetime(dia.year, dia.month, dia.day, 23, tzinfo=TZ_SPAIN)
    horas, t = set(), h0.astimezone(timezone.utc)
    fin = h23.astimezone(timezone.utc)
    while t <= fin:
        horas.add(t)
        t += timedelta(hours=1)
    return horas


# ── ESIOS ─────────────────────────────────────────────────────────────────────

def fetch_indicador(ind_id: int, dia: date, headers, log) -> dict:
    """Un indicador para un dia español, agregado a horario. Con margen para
    cubrir el desfase UTC/CEST de las horas frontera."""
    ini = dia - timedelta(days=1)
    fin = dia + timedelta(days=1)
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={ini}T00:00:00&end_date={fin}T23:59:59"
           f"&geo_ids[]={GEO_PENINSULA}"
           f"&time_trunc=hour&time_agg={TIME_AGG}")

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            r.raise_for_status()
            out = {}
            for v in r.json().get("indicator", {}).get("values", []):
                if v.get("value") is None:
                    continue
                dt = datetime.fromisoformat(v["datetime"].replace("Z", "+00:00"))
                out[dt.astimezone(timezone.utc)] = float(v["value"])
            return out
        except Exception as e:
            if intento < MAX_REINTENTOS:
                time.sleep(3 * intento)
            else:
                log.warning(f"    id {ind_id}: {str(e)[:70]}")
    return {}


# ── BD ────────────────────────────────────────────────────────────────────────

def estado_dia(conn, dia: date, log=None) -> dict:
    """Filas presentes y columnas con nulos, en UNA sola consulta."""
    horas = expected_hours_utc(dia)
    ini, fin = min(horas), max(horas)
    cols = list(INDICADORES)
    filtros = ", ".join(f"count({c}) AS n_{i}" for i, c in enumerate(cols))
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {filtros} FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        fila = cur.fetchone()
        cur.execute(f"SELECT datetime FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        existentes = {r[0] for r in cur.fetchall()}

    total = fila[0]
    n_esperado = len(horas)
    nulos = {c: n_esperado - fila[i + 1] for i, c in enumerate(cols)}
    incompletas = {c: n for c, n in nulos.items() if n > 0}

    criticos_ok = all(incompletas.get(c, 0) == 0 for c in CRITICOS)
    return {"horas": horas, "existentes": existentes, "total": total,
            "n_esperado": n_esperado, "incompletas": incompletas,
            "criticos_ok": criticos_ok}


def escribir(conn, dia: date, datos_por_col: dict, existentes: set) -> tuple:
    """Inserta filas nuevas y rellena NULLs, en lote por columna."""
    horas = expected_hours_utc(dia)
    nuevas = sorted(horas - existentes)
    ins = upd = 0

    if nuevas:
        with conn.cursor() as cur:
            execute_values(cur,
                f"INSERT INTO {TABLA} (datetime) VALUES %s "
                f"ON CONFLICT (datetime) DO NOTHING",
                [(h,) for h in nuevas], page_size=200)
        conn.commit()
        ins = len(nuevas)

    for col, datos in datos_por_col.items():
        registros = [(h, v) for h, v in datos.items() if h in horas]
        if not registros:
            continue
        with conn.cursor() as cur:
            execute_values(cur, f"""
                UPDATE {TABLA} AS t
                SET {col} = v.valor, updated_at = now()
                FROM (VALUES %s) AS v(ts, valor)
                WHERE t.datetime = v.ts AND t.{col} IS NULL
            """, registros, template="(%s, %s::numeric)", page_size=200)
            upd += cur.rowcount
        conn.commit()
    return ins, upd


# ── Carga de un dia ───────────────────────────────────────────────────────────

def cargar_dia(conn, dia: date, headers, log) -> tuple:
    est = estado_dia(conn, dia, log)

    if est["total"] >= est["n_esperado"] and not est["incompletas"]:
        log.info(f"  [{dia}] ya completo ({est['total']}h)")
        return 0, 0

    # Que columnas hace falta pedir: las que faltan, o todas si el dia es nuevo
    if est["total"] == 0:
        pedir = list(INDICADORES)
    else:
        pedir = [c for c in INDICADORES if c in est["incompletas"]]

    datos = {}
    for col in pedir:
        d = fetch_indicador(INDICADORES[col], dia, headers, log)
        time.sleep(PAUSA_SEC)
        if d:
            datos[col] = d

    ins, upd = escribir(conn, dia, datos, est["existentes"])

    est2 = estado_dia(conn, dia, log)
    faltan = est2["incompletas"]
    reales = {c: n for c, n in faltan.items() if c not in ESPORADICOS}
    marca = "✅ COMPLETO" if not reales else "⚠️ incompleto"
    log.info(f"  [{dia}] {est2['total']}/{est2['n_esperado']}h | "
             f"criticos={'✅' if est2['criticos_ok'] else '❌'} | {marca} "
             f"| {ins} insert, {upd} update")
    if reales:
        log.warning(f"    columnas con huecos: "
                    f"{ {c: n for c, n in sorted(reales.items())} }")
    return ins, upd


# ── Main ──────────────────────────────────────────────────────────────────────

def run(target: date | None = None):
    hoy = date.today()
    objetivo = target or (hoy - timedelta(days=1))
    log = setup_logger(hoy)

    log.info("=" * 66)
    log.info(f"ESIOS Generación Pipeline diario — {hoy}")
    log.info(f"Dia objetivo: {objetivo}")
    log.info(f"Tabla: {TABLA} | {len(INDICADORES)} indicadores | "
             f"geo_id={GEO_PENINSULA}")
    log.info(f"Agregacion: time_trunc=hour & time_agg={TIME_AGG}  (MW)")
    log.info(f"Revision: ultimos {DIAS_REVISION} dias")
    log.info("=" * 66)

    try:
        _, db_config = load_config()
        headers = get_headers()
    except Exception as e:
        log.error(f"Error de configuracion: {e}")
        return

    conn = None
    try:
        conn = psycopg2.connect(**db_config)

        log.info(f"\n=== PASO 1: dia objetivo ===")
        ins, upd = cargar_dia(conn, objetivo, headers, log)

        log.info(f"\n=== PASO 2: revision ultimos {DIAS_REVISION} dias ===")
        t_ins = t_upd = 0
        for k in range(1, DIAS_REVISION + 1):
            d = objetivo - timedelta(days=k)
            i, u = cargar_dia(conn, d, headers, log)
            t_ins += i
            t_upd += u
        log.info(f"  revision: {t_ins} insert, {t_upd} update")

    except Exception as e:
        log.error(f"Error: {e}")
    finally:
        if conn:
            conn.close()

    log.info("\nPipeline esios_gen finalizado")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pipeline diario esios_gen")
    p.add_argument("--fecha", help="YYYY-MM-DD (por defecto ayer)")
    args = p.parse_args()
    run(date.fromisoformat(args.fecha) if args.fecha else None)
