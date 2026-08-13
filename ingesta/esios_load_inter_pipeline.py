"""
TFM Energia UCM — ESIOS Load & Interconexiones Daily Pipeline
=============================================================
Carga diaria de esios_load_inter. Hermano de esios_load_inter_history.py, con
el que comparte el diccionario de indicadores y el reparto de agregaciones:
si cambia uno, hay que cambiar el otro.

QUE CARGA
  11 indicadores, geo_id=8741 (peninsula).
  Demanda (1293), tres saldos netos (10207-10209), saldo total (553) y las
  seis NTC de Francia, Portugal y Marruecos.

CUANDO EJECUTARSE
  Estos son datos de medida en tiempo real, no de programa: el dia D no esta
  completo hasta que termina. El cron va a las 01:30 hora de Madrid y carga
  D-1 completo. Un segundo pase a las 09:30 recupera lo que ESIOS publicase
  con retraso.
  IMPORTANTE: CRON_TZ=Europe/Madrid, no UTC. Si el cron corriese en UTC, en
  verano se lanzaria a las 03:30 de Madrid y en invierno a las 02:30, y el
  dia objetivo bailaria segun la estacion.

DOS AGREGACIONES DISTINTAS EN LA MISMA TABLA — el punto delicado
  time_agg=AVERAGE (8 indicadores, POTENCIA en MW)  [sin cambios]
      1293 demanda y 553 saldo total (nativos de 5 min) y las seis NTC
      (nativas de 15 min, pero una capacidad no se acumula a lo largo de la
      hora). Con sum salen infladas x4,00 EXACTO.
  resample horario por SUMA (3 indicadores, ENERGIA en MWh)
      Los tres saldos netos, nativos de 15 min.
  Meter un indicador en el grupo equivocado no da error: da un numero
  plausible y cuatro veces mayor. Es el error que obligo a recalcular 854.092
  celdas en esios_marketdata y 311.910 en esios_forecast_da.

CRITICOS Y ESPORADICOS
  Los once indicadores son CRITICOS: todos publican de forma continua. La lista
  de ESPORADICOS quedo vacia al eliminar los seis flujos direccionales, que
  eran los unicos con publicacion irregular.
  Se conserva el mecanismo porque el principio sigue vigente: la falta de dato
  en UNA variable no puede bloquear el dia ni generar reintentos indefinidos.
  De ahi TOLERANCIA_CRITICOS.

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

ANDORRA
  Sin indicadores. Los flujos 558/562 y el saldo 10210 devuelven vacio con
  cualquier geo_id y ademas son generacion medida con retardo M+1; las NTC
  491/495 no se publican desde el 21-abr-2020 (95,3% de NULLs sobre la serie).
  Las cinco columnas se eliminaron de la tabla. El intercambio con Andorra
  queda estimado por el residuo 553 - (fr + pt + ma), que NO es limpio: la
  mediana (-29 MW) es compatible, pero las colas llegan a miles de MW.

USO
    python -u esios_load_inter_pipeline.py              # D-1
    python -u esios_load_inter_pipeline.py --dia 2026-08-10
    python -u esios_load_inter_pipeline.py --dias 5     # ultimos 5 dias
    python -u esios_load_inter_pipeline.py --recalculo  # sobrescribe

CRON sugerido
    CRON_TZ=Europe/Madrid
    30 1 * * *  cd ~/scripts/ingesta && /usr/bin/python3 -u \
                esios_load_inter_pipeline.py >> ~/logs/cron_load_inter.log 2>&1
    30 9 * * *  cd ~/scripts/ingesta && /usr/bin/python3 -u \
                esios_load_inter_pipeline.py --dias 3 >> ~/logs/cron_load_inter.log 2>&1
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

# Este script vive en ingesta/, no en ingesta/historic_load/: la ruta base es
# parent, NO parent.parent (el historico usa parent.parent porque esta un nivel
# mas abajo). El sys.path.append tiene que ir ANTES del import de config.
BASE_DIR = Path(__file__).parent
sys.path.append(str(BASE_DIR))
from config import load_config

# ── Configuracion ─────────────────────────────────────────────────────────────

BASE_URL      = "https://api.esios.ree.es/indicators"
TABLA         = "esios_load_inter"
GEO_PENINSULA = 8741

MODO_RELLENAR = True          # False = sobrescribe (--recalculo)
DIAS_ATRAS    = 1             # D-1 por defecto

PAUSA_SEC      = 0.3
TIMEOUT_SEC    = 60
MAX_REINTENTOS = 3

TZ_SPAIN   = ZoneInfo("Europe/Madrid")
LOGS_DIR   = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
CREDS_PATH = BASE_DIR / "credentials.json"

# ── Indicadores ───────────────────────────────────────────────────────────────
# Identico a esios_load_inter_history.py. Cambiar los dos a la vez.

INDICADORES = {
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
    # Total
    "ree_netflow_total":  553,
}
# ree_gentotal es GENERATED ALWAYS AS (ree_load - ree_netflow_total) STORED:
# la calcula PostgreSQL sola, este script NO la escribe. Es la generacion
# nacional necesaria, no la demanda residual (que sera 1775 - 10358).

# ENERGIA en MWh cuarto-horaria -> SUMAR. El resto son MW -> PROMEDIAR.
COLUMNAS_SUMA = {
    "ree_netflow_fr", "ree_netflow_pt", "ree_netflow_ma",
}

# Su ausencia SI indica fallo: hay que reintentar el dia.
CRITICOS = {
    "ree_load", "ree_netflow_total",
    "ree_netflow_fr", "ree_netflow_pt", "ree_netflow_ma",
    "ree_ntc_impfr", "ree_ntc_expfr",
    "ree_ntc_imppt", "ree_ntc_exppt",
    "ree_ntc_impma", "ree_ntc_expma",
}

# Su ausencia NO indica fallo. Vacio desde que se eliminaron los flujos: los
# once indicadores restantes son todos criticos.
ESPORADICOS = set(INDICADORES) - CRITICOS

# Horas que se toleran ausentes en un CRITICO sin dar el dia por incompleto.
# El ultimo domingo de octubre el dia tiene 25 horas y ESIOS no publica una de
# las dos 02:00. Verificado sobre las 57.689 filas de la tabla: ocurre TODOS
# los años sin excepcion (2020-10-25, 2021-10-31, 2022-10-30, 2023-10-29,
# 2024-10-27, 2025-10-26) y son las UNICAS seis filas con algun NULL en las
# once columnas conservadas.
# En 2020-2024 solo afecta a ree_load y ree_netflow_total; en 2025 tambien a
# las seis NTC. Por eso la tolerancia es de UNA hora para TODOS los criticos y
# no solo para dos: con tolerancia 0 en las NTC, el 26-oct-2025 habria quedado
# marcado como incompleto y en reintento perpetuo.
TOLERANCIA_CRITICOS = {c: 1 for c in CRITICOS}
TOLERANCIA_DEFECTO = 0


def setup_logger(run_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"esios_load_inter_daily_{run_date}.log"
    logger = logging.getLogger(f"load_inter_daily_{run_date}")
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


# ── ESIOS ─────────────────────────────────────────────────────────────────────

def fetch_indicador(col: str, dia: date, headers, log) -> dict:
    """
    Un indicador para un dia, agregado a horario segun su naturaleza.
    Margen de un dia a cada lado: las 00:00 hora española son las 22:00 UTC del
    dia anterior en verano, y sin margen esas horas frontera quedaban fuera.
    """
    ind_id = INDICADORES[col]
    agg = "sum" if col in COLUMNAS_SUMA else "average"
    ini = dia - timedelta(days=1)
    fin = dia + timedelta(days=1)
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
                log.warning(f"    {col} (id {ind_id}): {str(e)[:70]}")
    return {}


# ── BD ────────────────────────────────────────────────────────────────────────

def estado_dia(conn, horas: set):
    """(horas_existentes, {columna: n_huecos}) en una sola pasada."""
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
    total = fila[0]
    huecos = {c: n - fila[i + 1] for i, c in enumerate(cols)
              if n - fila[i + 1] > 0}

    # Si el dia esta vacio, hay que pedir TODO, tambien los ESPORADICOS.
    # Es el bug que ya aparecio en el pipeline de PBF: en la primera carga de
    # un dia solo se pedian los CRITICOS y los demas no se descargaban nunca.
    if total == 0:
        huecos = {c: n for c in cols}
    return existentes, huecos


def escribir_dia(conn, horas: set, datos_por_col: dict, existentes: set):
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


def verificar_dia(conn, dia: date, horas: set, log) -> bool:
    """
    True si el dia se da por bueno. Un CRITICO por debajo de su tolerancia lo
    marca como incompleto; un ESPORADICO ausente se informa pero no bloquea.
    """
    ini, fin = min(horas), max(horas)
    cols = list(INDICADORES)
    conteos = ", ".join(f"count({c})" for c in cols)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        fila = cur.fetchone()

    n = len(horas)
    fallos, avisos = [], []
    for i, c in enumerate(cols):
        faltan = n - fila[i + 1]
        if faltan <= 0:
            continue
        if c in CRITICOS:
            tol = TOLERANCIA_CRITICOS.get(c, TOLERANCIA_DEFECTO)
            if faltan > tol:
                fallos.append(f"{c} (-{faltan}h, tolerancia {tol})")
            else:
                avisos.append(f"{c} -{faltan}h dentro de tolerancia")
        else:
            avisos.append(f"{c} -{faltan}h")

    if fila[0] < n:
        fallos.append(f"faltan {n - fila[0]} filas de {n}")

    for a in avisos:
        log.info(f"    aviso: {a}")
    if fallos:
        for f in fallos:
            log.warning(f"    FALTA: {f}")
        return False
    return True


def procesar_dia(conn, dia: date, headers, log) -> bool:
    horas = expected_hours_utc(dia)
    n_esperadas = len(horas)
    marca = ""
    if n_esperadas == 23:
        marca = "  (cambio de hora: dia de 23h)"
    elif n_esperadas == 25:
        marca = "  (cambio de hora: dia de 25h)"

    existentes, huecos = estado_dia(conn, horas)
    log.info(f"  {dia}: {n_esperadas} horas esperadas{marca}")

    pedir = list(INDICADORES) if not MODO_RELLENAR else list(huecos)
    if not pedir:
        log.info("    ya completo, no se pide nada")
        return True

    datos = {}
    for col in pedir:
        d = fetch_indicador(col, dia, headers, log)
        time.sleep(PAUSA_SEC)
        if d:
            datos[col] = d

    ins, upd = escribir_dia(conn, horas, datos, existentes)
    log.info(f"    {ins} ins / {upd} upd sobre {len(pedir)} indicadores")
    return verificar_dia(conn, dia, horas, log)


def main():
    p = argparse.ArgumentParser(description="Carga diaria esios_load_inter")
    p.add_argument("--dia", help="Dia concreto YYYY-MM-DD")
    p.add_argument("--dias", type=int, default=DIAS_ATRAS,
                   help="Ultimos N dias hasta D-1 (por defecto 1)")
    p.add_argument("--recalculo", action="store_true",
                   help="Sobrescribe en vez de rellenar solo los NULL")
    args = p.parse_args()

    global MODO_RELLENAR
    if args.recalculo:
        MODO_RELLENAR = False

    hoy = datetime.now(TZ_SPAIN).date()
    if args.dia:
        dias = [date.fromisoformat(args.dia)]
    else:
        dias = [hoy - timedelta(days=k) for k in range(args.dias, 0, -1)]

    log = setup_logger(hoy)
    _, db_config = load_config()
    headers = get_headers()
    conn = psycopg2.connect(**db_config)

    n_sum = len(COLUMNAS_SUMA)
    log.info("=" * 74)
    log.info("Carga diaria esios_load_inter")
    log.info(f"  Dias        : {dias[0]} a {dias[-1]}  ({len(dias)})")
    log.info(f"  Indicadores : {len(INDICADORES)}  |  geo_id={GEO_PENINSULA}")
    log.info(f"  Agregacion  : {n_sum} con sum (MWh) y "
             f"{len(INDICADORES)-n_sum} con average (MW)")
    log.info(f"  Criticos    : {len(CRITICOS)}  |  Esporadicos: {len(ESPORADICOS)}")
    log.info(f"  Modo        : {'RELLENAR NULLs' if MODO_RELLENAR else 'SOBRESCRIBIR'}")
    log.info("=" * 74)

    t0 = time.time()
    ok, ko = [], []
    for d in dias:
        try:
            (ok if procesar_dia(conn, d, headers, log) else ko).append(d)
        except Exception as e:
            log.error(f"  {d}: {e}")
            ko.append(d)
            conn.rollback()

    conn.close()
    log.info("=" * 74)
    log.info(f"Completos: {len(ok)}  |  Incompletos: {len(ko)}  "
             f"|  {(time.time()-t0)/60:.1f} min")
    if ko:
        log.warning(f"Dias a reintentar: {', '.join(str(d) for d in ko)}")
    # Codigo de salida != 0 para que el cron lo delate en el log
    sys.exit(1 if ko else 0)


if __name__ == "__main__":
    main()