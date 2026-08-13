"""
TFM Energia UCM — ESIOS Load & Interconexiones Historic Loader
==============================================================
Carga historica de esios_load_inter 2020-2026. Complementa el traslado ya hecho
desde esios_marketdata, que aporto 57.688 filas (2020-01-01 a 2026-07-31) con
la demanda, los tres saldos netos, el saldo total y las seis NTC de Francia,
Portugal y Marruecos.

QUE FALTA POR CARGAR
  Los seis flujos direccionales (556, 557, 559, 560, 561, 563) y el tramo final
  que falta en esios_marketdata.

DOS AGREGACIONES DISTINTAS EN LA MISMA TABLA — el punto delicado
A diferencia de esios_gen, donde los 16 indicadores eran MW de 5 minutos y
todo iba con average, aqui conviven dos familias:

  time_agg=AVERAGE (8 indicadores, son POTENCIA en MW)
      1293 demanda y 553 saldo total: nativos de 5 minutos.
      488-494 las seis NTC: nativas de 15 minutos, pero una capacidad de
      intercambio es potencia y no se acumula a lo largo de la hora.
      VERIFICADO en el TEST 506: con sum salen infladas x4,00 EXACTO. El
      indicador 490 daba 2400 frente a los 600 MW reales de la interconexion
      con Marruecos, y el 494 daba 3600 frente a 900. Con average ambos
      clavan el nominal.

  resample("h").sum() (3 indicadores, son ENERGIA en MWh)
      10207-10209 los tres saldos netos: nativos de 15 minutos. Verificado en su dia con el 561: la suma de los cuatro
      cuartos de la hora 00:00 del 28-jul-2026 daba 1542,04.

Meter un indicador en el grupo equivocado NO da error: da un numero plausible
pero cuatro veces mayor o menor. Es el error que ya obligo a recalcular
854.092 celdas en esios_marketdata y 311.910 en esios_forecast_da.

FLUJOS DIRECCIONALES ELIMINADOS (13-ago-2026) — dos motivos independientes
  1. REDUNDANCIA TOTAL. Verificado sobre 18.180 horas con coincidencia del
     100,0% en las tres interconexiones:
         ree_flow_fres = GREATEST(0,  ree_netflow_fr)
         ree_flow_esfr = GREATEST(0, -ree_netflow_fr)
     No eran medidas direccionales fisicas: eran el saldo neto partido en su
     parte positiva y su parte negativa. Cero informacion sobre 10207-10209.
  2. DESCONTINUADOS. Cinco de los seis (556, 557, 559, 560, 563) tienen
     cobertura 0,0% desde el 21-abr-2020, la misma fecha en que ESIOS dejo de
     publicar las NTC de Andorra. El sexto (561, ES->PT) sigue vivo pero
     publica 0,0 en las horas de importacion, no NULL: su cobertura aparente
     del 93-100% eran ceros. Es exactamente GREATEST(0, -10208), lo que cierra
     de paso la duda pendiente sobre saldo_portugal_exp_mw.

  Efecto colateral que motivo la decision: una columna que ESIOS ya no publica
  nunca sale de huecos_por_columna(), asi que ningun bloque se omitia jamas y
  cada pasada repetia seis peticiones vacias por bloque (~24 min de la carga
  completa). En el diario habrian sido seis llamadas inutiles cada madrugada.

  Si se necesita la vista direccional para un grafico o para la memoria, sale
  con SQL sin columna:  GREATEST(0, ree_netflow_fr) AS import_fr

ANDORRA — DESCARTADA POR COMPLETO (13-ago-2026)
Ninguno de sus indicadores sirve, y por dos motivos distintos:

  Flujos 558, 562 y saldo neto 10210: devuelven vacio con CUALQUIER geo_id
  (verificado en el TEST 506). Ademas son "generacion medida", que ESIOS
  publica a partir del mes M+1: aunque llegasen, no valen para el diario ni
  como feature, porque el dato del dia D no existe hasta 30-60 dias despues.

  NTC 491 y 495: ESIOS DEJO DE PUBLICARLAS el 21-abr-2020. Verificado sobre
  las 57.689 horas de la tabla: 55.002 (95,3%) son NULL. Solo hay dato de
  2020-01-01 a 2020-03-31 (133 MW) y de 2020-04-01 a 2020-04-21 (107 MW).

Las cinco columnas se eliminaron de la tabla. El intercambio con Andorra queda
estimado UNICAMENTE por el residuo 553 - (saldo_fr + saldo_pt + saldo_ma), que
esta funcion cuadre_balance() cuantifica al final de cada ejecucion.

NOTA sobre las NTC que SI se quedan: no son constantes ni estan cruzadas.
Marruecos tiene 515 combinaciones imp/exp distintas, con 600/900 dominante
(64,9% de las horas) y exportacion anulada a 0 en el 8,2%. El bloque 400/400
es el regimen real anterior al 4-abr-2020 mas episodios de restriccion
aislados, NO un valor de relleno. Francia y Portugal difieren entre imp y exp
en el 96,8% y 98,4% de las horas respectivamente: el mapeo id->columna esta
verificado en toda la tabla.

USO
    python -u esios_load_inter_history.py --desde 2026-08-01 --hasta 2026-08-07
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
    "ree_netflow_fr":   10207,
    "ree_ntc_impfr":      488,
    "ree_ntc_expfr":      492,
    # Portugal
    "ree_netflow_pt":   10208,
    "ree_ntc_imppt":      489,
    "ree_ntc_exppt":      493,
    # Marruecos
    "ree_netflow_ma":   10209,
    "ree_ntc_impma":      490,
    "ree_ntc_expma":      494,
    # Andorra: sin indicadores. Flujos 558/562/10210 vacios y con retardo M+1;
    # NTC 491/495 sin publicar desde el 21-abr-2020. Ver docstring.
    # Total
    "ree_netflow_total":  553,
}
# ree_gentotal es GENERATED ALWAYS AS (ree_load - ree_netflow_total) STORED:
# la calcula PostgreSQL sola, este script NO la escribe. Es la generacion
# nacional necesaria, no la demanda residual (que sera 1775 - 10358).

# ENERGIA en MWh cuarto-horaria: hay que SUMAR los cuatro cuartos de la hora.
# El resto son POTENCIA en MW y se PROMEDIAN.
COLUMNAS_SUMA = {
    "ree_netflow_fr", "ree_netflow_pt", "ree_netflow_ma",
}

# Columnas cuya ausencia NO indica fallo. Vacio: eliminados los flujos, los
# once indicadores restantes publican de forma continua.
ESPORADICOS = set()


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
    Residuo 553 - (saldo_fr + saldo_pt + saldo_ma). Es el UNICO estimador del
    intercambio con Andorra desde que se descartaron sus cinco columnas.

    Con SIGNO, no en valor absoluto: importa saber si Andorra es import o
    export sistematico, y el abs() lo ocultaba.
    Exige las CUATRO columnas NOT NULL: con coalesce(...,0) una hora sin saldo
    de Portugal producia un residuo de miles de MW que contaminaba la media.

    Las unidades cuadran pese a mezclar agregaciones: los tres saldos son MWh
    horarios (suma de los cuatro cuartos) y el 553 es MW medios horarios, y
    para una hora ambas magnitudes coinciden numericamente.
    """
    log.info("\n" + "=" * 74)
    log.info("RESIDUO DE ANDORRA: 553 - (saldo_fr + saldo_pt + saldo_ma)")
    log.info("=" * 74)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT round(avg(dif)::numeric, 2),
                       round(stddev(dif)::numeric, 2),
                       round(min(dif)::numeric, 2),
                       round(max(dif)::numeric, 2),
                       round(avg(abs(dif))::numeric, 2),
                       count(*)
                FROM (
                    SELECT ree_netflow_total
                         - (ree_netflow_fr + ree_netflow_pt + ree_netflow_ma)
                           AS dif
                    FROM {TABLA}
                    WHERE ree_netflow_total IS NOT NULL
                      AND ree_netflow_fr    IS NOT NULL
                      AND ree_netflow_pt    IS NOT NULL
                      AND ree_netflow_ma    IS NOT NULL
                ) s
            """)
            media, sd, mn, mx, mabs, n = cur.fetchone()

        if not n:
            log.warning("  Sin horas con los cuatro saldos disponibles.")
            return

        log.info(f"  Media (con signo) : {media} MW")
        log.info(f"  Media absoluta    : {mabs} MW")
        log.info(f"  Desv. tipica      : {sd} MW")
        log.info(f"  Rango             : {mn} a {mx} MW")
        log.info(f"  Horas             : {n:,}")

        with conn.cursor() as cur2:
            cur2.execute(f"""
                SELECT to_char(date_trunc('year', datetime), 'YYYY') AS anio,
                       round(avg(dif)::numeric, 2),
                       round(avg(abs(dif))::numeric, 2),
                       count(*)
                FROM (
                    SELECT datetime,
                           ree_netflow_total
                         - (ree_netflow_fr + ree_netflow_pt + ree_netflow_ma)
                           AS dif
                    FROM {TABLA}
                    WHERE ree_netflow_total IS NOT NULL
                      AND ree_netflow_fr    IS NOT NULL
                      AND ree_netflow_pt    IS NOT NULL
                      AND ree_netflow_ma    IS NOT NULL
                ) s
                GROUP BY 1 ORDER BY 1
            """)
            log.info("")
            log.info(f"  {'anio':<6} {'media':>10} {'media_abs':>11} {'horas':>8}")
            for anio, med, mab, cnt in cur2.fetchall():
                log.info(f"  {anio:<6} {med:>10} {mab:>11} {cnt:>8,}")

        log.info("")
        # La NTC de Andorra era 133 MW: un residuo que se sale de ese orden de
        # magnitud NO puede ser esa interconexion. La media puede parecer sana
        # y la desviacion delatar horas con cuartos incompletos, asi que se
        # vigilan las dos cosas por separado.
        ok_media = mabs is not None and abs(mabs) < 100
        ok_disp  = sd is not None and abs(sd) < 100
        if ok_media and ok_disp:
            log.info("  Magnitud y dispersion compatibles con Andorra.")
            log.info("  Si es estable año a año, el residuo sirve como estimador")
            log.info("  y puede citarse asi en el capitulo de datos.")
        else:
            if not ok_media:
                log.warning(f"  MEDIA ALTA ({mabs} MW): hay un segundo termino")
                log.warning("  ademas de Andorra.")
            if not ok_disp:
                log.warning(f"  DISPERSION ALTA (sd {sd} MW, rango {mn} a {mx}):")
                log.warning("  tipico de horas con cuartos incompletos. Los tres")
                log.warning("  saldos van con sum y el 553 con average: si a una")
                log.warning("  hora le faltan cuartos, la suma se queda corta y")
                log.warning("  la media no se entera.")
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