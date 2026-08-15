"""
TFM Energia UCM — ESIOS Generación Historic Loader
==================================================
Carga historica de esios_gen 2020-2026. Complementa el traslado ya hecho desde
esios_marketdata, que aporto 57.688 filas (2020-01-01 a 2026-07-31) con diez
columnas ya saneadas: solar FV, termosolar, eolica, hidraulica, bombeo
(generacion y consumo), nuclear, ciclo combinado, carbon y cogeneracion+resto.

QUE FALTA POR CARGAR
  Las seis columnas que no existen en esios_marketdata:
      ree_gtermicarenew_mw   1296   biogas+biomasa+oceano+geotermica
      ree_grenew_mw         10351   agregado oficial REE
      ree_gnorenew_mw       10352   agregado oficial REE
      ree_gbattery_mw        2167   entrega baterias (desde 2025)
      ree_cbattery_mw        2166   carga baterias (desde 2025)
      ree_goil_mw             548   fuel-gas (residual, probablemente vacio)
  Y el tramo final que falta en esios_marketdata por el corte del pipeline.

MODO_RELLENAR (True por defecto) escribe solo donde la columna esta NULL, asi
que no toca lo trasladado y en cada chunk pide UNICAMENTE las columnas con
huecos. Con --recalculo sobrescribe todo, util para corregir una carga previa.

AGREGACION — time_agg=average
Los 16 indicadores son POTENCIA (MW) con 288 valores nativos al dia (5 min),
asi que el valor horario es el promedio de las 12 muestras. Verificado en el
TEST 504 sobre el indicador 1295: la media manual de las 12 muestras coincide
al centimo con lo que devuelve la API.
Es lo CONTRARIO que esios_pbf_*, donde los programas son MWh cuarto-horarios y
usan sum. Y sin especificar time_agg, ESIOS agrega por SUMA por defecto: eso
inflo en su dia esios_marketdata x11-12 y esios_forecast_da x4.

DOS PARTICULARIDADES DE LOS DATOS
  ree_ghidro_mw (546) viene NETEADA de bombeo y llega a -3.299 MW a mediodia,
  asi que NO es sumable con ree_cpumping_mw. Y ree_cpumping_mw / ree_cbattery_mw
  vienen NEGATIVOS desde ESIOS, mientras en entsoe_gen_data los consumos son
  positivos: se guardan tal cual llegan.

USO
    python esios_gen_history.py                    # 2020 -> hoy
    python esios_gen_history.py --desde 2026-07-25 # solo un tramo
    python esios_gen_history.py --recalculo        # sobrescribe
Conviene lanzarlo con nohup y python -u para ver el log en tiempo real.
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

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ── Configuracion ─────────────────────────────────────────────────────────────

BASE_URL      = "https://api.esios.ree.es/indicators"
TABLA         = "esios_gen"
GEO_PENINSULA = 8741
TIME_AGG      = "average"          # MW: promedio. NO sum (ver docstring)

START_DATE = "2020-01-01"
END_DATE   = date.today().strftime("%Y-%m-%d")

# True  = solo rellena NULLs (respeta lo trasladado desde esios_marketdata)
# False = sobrescribe siempre
MODO_RELLENAR = True

CHUNK_DAYS    = 7
PAUSA_SEC     = 0.3
TIMEOUT_SEC   = 60
MAX_REINTENTOS = 3

TZ_SPAIN = ZoneInfo("Europe/Madrid")
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
CREDS_PATH = Path(__file__).parent.parent / "credentials.json"

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

def horas_del_rango(desde: date, hasta: date) -> set:
    """Todas las horas UTC de los dias españoles del rango."""
    horas, d = set(), desde
    while d <= hasta:
        horas |= expected_hours_utc(d)
        d += timedelta(days=1)
    return horas


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

def fetch_indicador(ind_id: int, desde: date, hasta: date, headers, log) -> dict:
    """
    Un indicador para un rango de dias, agregado a horario.
    Con un dia de margen a cada lado: las 00:00 hora española son las 22:00 UTC
    del dia anterior en verano, y sin margen esas horas frontera quedaban fuera.
    """
    ini = desde - timedelta(days=1)
    fin = hasta + timedelta(days=1)
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

def huecos_por_columna(conn, horas: set):
    """
    (horas_existentes, {columna: n_huecos}) para el rango, en UNA consulta.
    Un count() por columna en vez de una consulta por celda: con la latencia
    del servidor, la version por filas tardaria horas.
    """
    ini, fin = min(horas), max(horas)
    cols = list(INDICADORES)
    conteos = ", ".join(f"count({c})" for c in cols)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        fila = cur.fetchone()
        cur.execute(f"SELECT datetime FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        existentes = {r[0] for r in cur.fetchall()}

    n = len(horas)
    huecos = {c: n - fila[i + 1] for i, c in enumerate(cols)
              if n - fila[i + 1] > 0}
    return existentes, huecos


def escribir_chunk(conn, horas: set, datos_por_col: dict, existentes: set):
    """Inserta las filas que faltan y rellena las columnas, en lote."""
    nuevas = sorted(horas - existentes)
    ins = upd = 0

    if nuevas:
        with conn.cursor() as cur:
            execute_values(cur,
                f"INSERT INTO {TABLA} (datetime) VALUES %s "
                f"ON CONFLICT (datetime) DO NOTHING",
                [(h,) for h in nuevas], page_size=500)
        conn.commit()
        ins = len(nuevas)

    for col, datos in datos_por_col.items():
        registros = [(h, round(v, 2)) for h, v in datos.items() if h in horas]
        if not registros:
            continue
        # Con MODO_RELLENAR se respeta lo ya cargado; si no, se pisa.
        filtro = f" AND t.{col} IS NULL" if MODO_RELLENAR else ""
        with conn.cursor() as cur:
            execute_values(cur, f"""
                UPDATE {TABLA} AS t
                SET {col} = v.valor, updated_at = now()
                FROM (VALUES %s) AS v(ts, valor)
                WHERE t.datetime = v.ts{filtro}
            """, registros, template="(%s, %s::numeric)", page_size=500)
            upd += cur.rowcount
        conn.commit()
    return ins, upd


def bloques(desde: date, hasta: date, dias: int):
    out, cur = [], desde
    while cur <= hasta:
        fin = min(cur + timedelta(days=dias - 1), hasta)
        out.append((cur, fin))
        cur = fin + timedelta(days=1)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def estado_final(conn, log):
    cols = list(INDICADORES)
    conteos = ", ".join(f"count({c})" for c in cols)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), min(datetime)::date, max(datetime)::date, "
                    f"{conteos} FROM {TABLA}")
        fila = cur.fetchone()
    total, d1, d2 = fila[0], fila[1], fila[2]
    log.info("\n" + "=" * 70)
    log.info(f"ESTADO FINAL — {total:,} filas, {d1} a {d2}")
    log.info("=" * 70)
    for i, c in enumerate(cols):
        n = fila[i + 3]
        pct = n / total * 100 if total else 0
        marca = "  (esporadica: hueco esperado)" if c in ESPORADICOS and pct < 95 else ""
        log.info(f"  {c:<24} {n:>8,} / {total:,}  {pct:>5.1f}%{marca}")


def main():
    p = argparse.ArgumentParser(description="Carga historica esios_gen")
    p.add_argument("--desde", default=START_DATE)
    p.add_argument("--hasta", default=END_DATE)
    p.add_argument("--recalculo", action="store_true",
                   help="Sobrescribe en vez de rellenar solo los NULL")
    args = p.parse_args()

    global MODO_RELLENAR
    if args.recalculo:
        MODO_RELLENAR = False

    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    log = setup_logger(date.today())
    _, db_config = load_config()
    headers = get_headers()
    conn = psycopg2.connect(**db_config)

    chunks = bloques(desde, hasta, CHUNK_DAYS)
    log.info("=" * 70)
    log.info("Carga historica esios_gen")
    log.info(f"  Periodo     : {desde} a {hasta}")
    log.info(f"  Bloques     : {len(chunks)} de {CHUNK_DAYS} dias")
    log.info(f"  Indicadores : {len(INDICADORES)}  |  geo_id={GEO_PENINSULA}")
    log.info(f"  Agregacion  : time_trunc=hour & time_agg={TIME_AGG}  (MW)")
    log.info(f"  Modo        : {'RELLENAR NULLs' if MODO_RELLENAR else 'SOBRESCRIBIR'}")
    log.info("=" * 70)

    t0 = time.time()
    for i, (c_ini, c_fin) in enumerate(chunks, 1):
        t_chunk = time.time()
        horas = horas_del_rango(c_ini, c_fin)
        existentes, huecos = huecos_por_columna(conn, horas)

        # Solo se piden las columnas con huecos: tras el traslado desde
        # esios_marketdata, en la mayoria de chunks son solo las seis nuevas.
        pedir = list(INDICADORES) if not MODO_RELLENAR else list(huecos)
        if not pedir:
            log.info(f"[{i}/{len(chunks)}] {c_ini} a {c_fin}: completo, omitido")
            continue

        datos = {}
        for col in pedir:
            d = fetch_indicador(INDICADORES[col], c_ini, c_fin, headers, log)
            time.sleep(PAUSA_SEC)
            if d:
                datos[col] = d

        ins, upd = escribir_chunk(conn, horas, datos, existentes)
        pct = i / len(chunks) * 100
        eta = (time.time() - t0) / i * (len(chunks) - i) / 60
        log.info(f"[{i}/{len(chunks)}] {c_ini} a {c_fin}: "
                 f"{ins} ins / {upd} upd, {len(pedir)} cols "
                 f"({time.time()-t_chunk:.0f}s | {pct:.0f}% | ETA {eta:.0f} min)")

    estado_final(conn, log)
    conn.close()
    log.info("=" * 70)
    log.info(f"Duracion: {(time.time()-t0)/60:.0f} min")


def _run_diario(target: date | None = None):
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
    main()
