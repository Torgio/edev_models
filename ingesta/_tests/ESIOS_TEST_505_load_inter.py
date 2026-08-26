"""
TFM Energia UCM — ESIOS Load & Interconexiones Historic Loader
==============================================================
Carga historica de esios_load_inter 2020-2026. Complementa el traslado ya hecho
desde esios_marketdata, que aporto 57.688 filas (2020-01-01 a 2026-07-31) con
la demanda, los tres saldos netos, el saldo total y las seis NTC de Francia,
Portugal y Marruecos.

QUE FALTA POR CARGAR
  Los seis flujos direccionales (556, 557, 559, 560, 561, 563), las dos NTC de
  Andorra (491, 495) y el tramo final que falta en esios_marketdata.

DOS AGREGACIONES DISTINTAS EN LA MISMA TABLA — el punto delicado
A diferencia de esios_gen, donde los 16 indicadores eran MW de 5 minutos y
todo iba con average, aqui conviven dos familias:

  time_agg=AVERAGE (10 indicadores, son POTENCIA en MW)
      1293 demanda y 553 saldo total: nativos de 5 minutos.
      488-495 las ocho NTC: nativas de 15 minutos, pero una capacidad de
      intercambio es potencia y no se acumula a lo largo de la hora.
      VERIFICADO en el TEST 506: con sum salen infladas x4,00 EXACTO. El
      indicador 490 daba 2400 frente a los 600 MW reales de la interconexion
      con Marruecos, y el 494 daba 3600 frente a 900. Con average ambos
      clavan el nominal.

  resample("h").sum() (9 indicadores, son ENERGIA en MWh)
      556-563 los seis flujos y 10207-10209 los tres saldos netos: nativos de
      15 minutos. Verificado en su dia con el 561: la suma de los cuatro
      cuartos de la hora 00:00 del 28-jul-2026 daba 1542,04.

Meter un indicador en el grupo equivocado NO da error: da un numero plausible
pero cuatro veces mayor o menor. Es el error que ya obligo a recalcular
854.092 celdas en esios_marketdata y 311.910 en esios_forecast_da.

SOBRE LOS HUECOS DE LOS FLUJOS DIRECCIONALES
ESIOS publica el punto SOLO cuando hay flujo en ese sentido. Verificado en seis
fechas: el 15-ene-2026 el indicador 560 (ES->FR) no tenia ningun valor y el 556
(FR->ES) tenia 96, porque España importo todo el dia; en abril se invirtio, 78
y 18. Ambos suman siempre ~96 cuartos de hora.
DECISION: los huecos se dejan como NULL, no se rellenan con cero. Un NULL aqui
significa "no hubo flujo en ese sentido", y eso hay que documentarlo para que
al construir features nadie lo confunda con un dato ausente. Rellenar con cero
seria inventar un dato que ESIOS no publico.

ANDORRA
Solo sus NTC (491, 495) estan disponibles. Los flujos 558 y 562 y el saldo neto
10210 devuelven vacio con CUALQUIER geo_id, no solo con el peninsular
(verificado en el TEST 506). Por eso la tabla no tiene columnas de flujo con
Andorra, y el residuo de decenas de MW que aparece al contrastar el 553 contra
la suma de los otros tres saldos queda explicado pero no cuantificado.

USO
    python esios_load_inter_history.py --desde 2026-08-01 --hasta 2026-08-07
    python esios_load_inter_history.py                    # 2020 -> hoy
    python esios_load_inter_history.py --recalculo        # sobrescribe
Conviene lanzarlo con nohup y python -u para seguir el log en tiempo real.
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
TABLA         = "esios_load_inter"
GEO_PENINSULA = 8741

START_DATE = "2020-01-01"
END_DATE   = date.today().strftime("%Y-%m-%d")

# True  = solo rellena NULLs (respeta lo trasladado desde esios_marketdata)
# False = sobrescribe siempre
MODO_RELLENAR = True

CHUNK_DAYS     = 7
PAUSA_SEC      = 0.3
TIMEOUT_SEC    = 60
MAX_REINTENTOS = 3

TZ_SPAIN = ZoneInfo("Europe/Madrid")
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
CREDS_PATH = Path(__file__).parent.parent / "credentials.json"

# ── Indicadores ───────────────────────────────────────────────────────────────

INDICADORES = {
    # Demanda
    "ree_load":          1293,
    # Francia
    "ree_flow_esfr":      560,
    "ree_flow_fres":      556,
    "ree_netflow_fr":   10207,
    "ree_ntc_impfr":      488,
    "ree_ntc_expfr":      492,
    # Portugal
    "ree_flow_espt":      561,
    "ree_flow_ptes":      557,
    "ree_netflow_pt":   10208,
    "ree_ntc_imppt":      489,
    "ree_ntc_exppt":      493,
    # Marruecos
    "ree_flow_esma":      563,
    "ree_flow_maes":      559,
    "ree_netflow_ma":   10209,
    "ree_ntc_impma":      490,
    "ree_ntc_expma":      494,
    # Andorra: solo NTC (los flujos 558/562 y el saldo 10210 no publican)
    "ree_ntc_impad":      491,
    "ree_ntc_expad":      495,
    # Total
    "ree_netflow_total":  553,
}
# ree_netload es GENERATED: la calcula PostgreSQL, no se escribe aqui.

# ENERGIA en MWh cuarto-horaria: hay que SUMAR los cuatro cuartos de la hora.
# El resto son POTENCIA en MW y se PROMEDIAN.
COLUMNAS_SUMA = {
    "ree_flow_esfr", "ree_flow_fres", "ree_netflow_fr",
    "ree_flow_espt", "ree_flow_ptes", "ree_netflow_pt",
    "ree_flow_esma", "ree_flow_maes", "ree_netflow_ma",
}

# Columnas cuya ausencia NO indica fallo
ESPORADICOS = {
    # Publican solo cuando hay flujo en ese sentido
    "ree_flow_esfr", "ree_flow_fres", "ree_flow_espt",
    "ree_flow_ptes", "ree_flow_esma", "ree_flow_maes",
}


def setup_logger(run_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"esios_load_inter_{run_date}.log"
    logger = logging.getLogger(f"esios_load_inter_{run_date}")
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
    Horas UTC del dia ESPAÑOL. En los cambios de hora el dia tiene 23 o 25
    horas: generar 24 timestamps fijos haria que en marzo el pipeline
    reintentase eternamente y en octubre diese el dia por completo con una
    hora de menos.
    """
    h0  = datetime(dia.year, dia.month, dia.day, 0, tzinfo=TZ_SPAIN)
    h23 = datetime(dia.year, dia.month, dia.day, 23, tzinfo=TZ_SPAIN)
    horas, t = set(), h0.astimezone(timezone.utc)
    fin = h23.astimezone(timezone.utc)
    while t <= fin:
        horas.add(t)
        t += timedelta(hours=1)
    return horas


def horas_del_rango(desde: date, hasta: date) -> set:
    horas, d = set(), desde
    while d <= hasta:
        horas |= expected_hours_utc(d)
        d += timedelta(days=1)
    return horas


# ── ESIOS ─────────────────────────────────────────────────────────────────────

def fetch_indicador(col: str, desde: date, hasta: date, headers, log) -> dict:
    """
    Un indicador para un rango, agregado a horario segun su naturaleza.
    Margen de un dia a cada lado: las 00:00 hora española son las 22:00 UTC del
    dia anterior en verano, y sin margen esas horas frontera quedaban fuera.
    """
    ind_id = INDICADORES[col]
    agg = "sum" if col in COLUMNAS_SUMA else "average"
    ini = desde - timedelta(days=1)
    fin = hasta + timedelta(days=1)
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={ini}T00:00:00&end_date={fin}T23:59:59"
           f"&geo_ids[]={GEO_PENINSULA}"
           f"&time_trunc=hour&time_agg={agg}")

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
                log.warning(f"    {col} (id {ind_id}): {str(e)[:60]}")
    return {}


# ── BD ────────────────────────────────────────────────────────────────────────

def huecos_por_columna(conn, horas: set):
    """(horas_existentes, {columna: n_huecos}) en UNA sola consulta."""
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
        registros = [(h, v) for h, v in datos.items() if h in horas]
        if not registros:
            continue
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


# ── Estado final ──────────────────────────────────────────────────────────────

def estado_final(conn, log):
    cols = list(INDICADORES)
    conteos = ", ".join(f"count({c})" for c in cols)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), min(datetime)::date, max(datetime)::date, "
                    f"{conteos} FROM {TABLA}")
        fila = cur.fetchone()
    total, d1, d2 = fila[0], fila[1], fila[2]
    log.info("\n" + "=" * 74)
    log.info(f"ESTADO FINAL — {total:,} filas, {d1} a {d2}")
    log.info("=" * 74)
    for i, c in enumerate(cols):
        n = fila[i + 3]
        pct = n / total * 100 if total else 0
        agg = "sum" if c in COLUMNAS_SUMA else "avg"
        marca = ""
        if c in ESPORADICOS and pct < 95:
            marca = "  (solo publica cuando hay flujo en ese sentido)"
        log.info(f"  {c:<20} [{agg}] {n:>8,} / {total:,}  {pct:>5.1f}%{marca}")


def cuadre_balance(conn, log):
    """
    La suma de los tres saldos netos frente al 553. La diferencia deberia ser
    pequeña y corresponder a Andorra, que ESIOS no publica.
    """
    log.info("\n" + "=" * 74)
    log.info("CUADRE: suma de saldos frente al saldo total (553)")
    log.info("=" * 74)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT round(avg(abs(
                         coalesce(ree_netflow_fr,0) + coalesce(ree_netflow_pt,0)
                       + coalesce(ree_netflow_ma,0) - coalesce(ree_netflow_total,0)
                       ))::numeric, 1),
                       count(*)
                FROM {TABLA}
                WHERE ree_netflow_total IS NOT NULL
                  AND ree_netflow_fr IS NOT NULL
            """)
            dif, n = cur.fetchone()
        log.info(f"  Diferencia media: {dif} MW sobre {n:,} horas")
        log.info("  Esa diferencia es Andorra, que ESIOS no publica para ningun")
        log.info("  geo_id (verificado con los indicadores 558, 562 y 10210).")
    except Exception as e:
        log.warning(f"  No se pudo calcular: {e}")


def main():
    p = argparse.ArgumentParser(description="Carga historica esios_load_inter")
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
    n_sum = len(COLUMNAS_SUMA)
    log.info("=" * 74)
    log.info("Carga historica esios_load_inter")
    log.info(f"  Periodo     : {desde} a {hasta}")
    log.info(f"  Bloques     : {len(chunks)} de {CHUNK_DAYS} dias")
    log.info(f"  Indicadores : {len(INDICADORES)}  |  geo_id={GEO_PENINSULA}")
    log.info(f"  Agregacion  : {n_sum} con sum (MWh) y "
             f"{len(INDICADORES)-n_sum} con average (MW)")
    log.info(f"  Modo        : {'RELLENAR NULLs' if MODO_RELLENAR else 'SOBRESCRIBIR'}")
    log.info("=" * 74)

    t0 = time.time()
    for i, (c_ini, c_fin) in enumerate(chunks, 1):
        t_chunk = time.time()
        horas = horas_del_rango(c_ini, c_fin)
        existentes, huecos = huecos_por_columna(conn, horas)

        pedir = list(INDICADORES) if not MODO_RELLENAR else list(huecos)
        if not pedir:
            log.info(f"[{i}/{len(chunks)}] {c_ini} a {c_fin}: completo, omitido")
            continue

        datos = {}
        for col in pedir:
            d = fetch_indicador(col, c_ini, c_fin, headers, log)
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
    cuadre_balance(conn, log)
    conn.close()
    log.info("=" * 74)
    log.info(f"Duracion: {(time.time()-t0)/60:.0f} min")


if __name__ == "__main__":
    main()