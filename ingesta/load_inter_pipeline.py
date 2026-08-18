"""
TFM Energia UCM — Load & Interconexiones Daily Pipeline (unificado)
==================================================================
Carga diaria de load_inter, tabla que sustituye a esios_load_inter y a la
parte de demanda de entsoe_load_inter. Un solo script para las dos APIs.

QUE CARGA
  11 columnas base:
    ENTSO-E (1)  entsoe_load        Actual Total Load, zona ES
    ESIOS  (10)  ree_load           demanda peninsular (1293)
                 ree_netflow_fr/pt/ma   saldos netos (10207/10208/10209)
                 ree_ntc_imp/exp x3     NTC (488-490 / 492-494)
  Dos columnas mas las calcula PostgreSQL y este script NO las escribe:
    total_net_flow_mw  = fr + pt + ma
    gen_peninsular_mw  = entsoe_load - total_net_flow_mw
  Sin COALESCE deliberadamente: las horas sin demanda deben propagar NULL,
  no fabricar un valor plausible. Es el fallo de entsoe_load_inter.net_load_mw,
  que durante el apagon devuelve 0-18 MW en vez de NULL.

POR QUE LOS FLUJOS VIENEN DE ESIOS Y LA DEMANDA DE ENTSO-E
  Marruecos no es miembro de ENTSO-E: sus flujos solo existen en ESIOS.
  Las NTC de ENTSO-E tienen un hueco desde dic-2023 (~976 dias); las de ESIOS
  estan completas desde nov-2020.
  La demanda se toma de ENTSO-E porque ree_load incorpora la estimacion de
  autoconsumo desde dic-2025 y deja de ser homogenea. entsoe_load mide lo
  mismo en todo el rango 2020-2026. ree_load se conserva como columna
  documental: su diferencia con entsoe_load estima el autoconsumo peninsular,
  magnitud que ninguna fuente publica.

DOS AGREGACIONES DISTINTAS EN LA MISMA TABLA — el punto delicado
  time_agg=AVERAGE (7 indicadores ESIOS, POTENCIA en MW)
      1293 demanda (nativo de 5 min) y las seis NTC (nativas de 15 min, pero
      una capacidad no se acumula a lo largo de la hora). Con sum salen
      infladas x4,00 EXACTO.
  time_agg=SUM (3 indicadores ESIOS, ENERGIA en MWh)
      Los tres saldos netos, nativos de 15 min.
  ENTSO-E: resample("h").mean(), y ANTES de filtrar al dia objetivo. Los datos
  son cuarto-horarios desde oct-2025 (MTU15); filtrando primero se descartan
  las muestras de :15/:30/:45 y queda una sola en vez del promedio.
  Meter un indicador en el grupo equivocado no da error: da un numero
  plausible y cuatro veces mayor.

CONVENIO DE SIGNO
  Positivo = importacion a España. Verificado empiricamente: media 2024+ de
  fr +144, pt -1.149, ma -399 MW. Los saldos FR+PT de ESIOS y ENTSO-E
  coinciden dentro de pocos MW.

CRITICOS Y TOLERANCIA
  Las once columnas son criticas. Tolerancia de UNA hora en todas: el ultimo
  domingo de octubre el dia tiene 25 horas y ESIOS no publica una de las dos
  02:00. Ocurre todos los años sin excepcion.

APAGON IBERICO (28-29 abr 2025)
  35 horas sin demanda en ENTSO-E, de 2025-04-28 13:00 a 2025-04-29 23:00.
  No es fallo de ingesta: no habia sistema que medir. Esos dos dias quedaran
  siempre marcados como incompletos en entsoe_load; es correcto y esperado.

USO
    python -u load_inter_pipeline.py              # D-1
    python -u load_inter_pipeline.py --dia 2026-08-10
    python -u load_inter_pipeline.py --dias 5     # ultimos 5 dias
    python -u load_inter_pipeline.py --recalculo  # sobrescribe en vez de rellenar
    python -u load_inter_pipeline.py --solo esios # una sola fuente

CRON sugerido
    CRON_TZ=Europe/Madrid
    30 1 * * *  cd ~/scripts/ingesta && /home/ubuntu/tfm-env/bin/python -u \
                load_inter_pipeline.py >> ~/logs/cron_load_inter.log 2>&1
    30 9 * * *  cd ~/scripts/ingesta && /home/ubuntu/tfm-env/bin/python -u \
                load_inter_pipeline.py --dias 3 >> ~/logs/cron_load_inter.log 2>&1
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

BASE_DIR = Path(__file__).parent
sys.path.append(str(BASE_DIR))
from config import load_config

# ── Configuracion ─────────────────────────────────────────────────────────────

BASE_URL      = "https://api.esios.ree.es/indicators"
TABLA         = "load_inter"
GEO_PENINSULA = 8741
COUNTRY       = "ES"

MODO_RELLENAR = True          # False = sobrescribe (--recalculo)
DIAS_ATRAS    = 1             # D-1 por defecto

PAUSA_SEC      = 0.3
PAUSA_API_SEC  = 1.0
TIMEOUT_SEC    = 60
MAX_REINTENTOS = 3

TZ_SPAIN   = ZoneInfo("Europe/Madrid")
LOGS_DIR   = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
CREDS_PATH = BASE_DIR / "credentials.json"

# ── Indicadores ESIOS ─────────────────────────────────────────────────────────

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
}

# ENERGIA en MWh cuarto-horaria -> SUMAR. El resto son MW -> PROMEDIAR.
COLUMNAS_SUMA = {"ree_netflow_fr", "ree_netflow_pt", "ree_netflow_ma"}

COL_ENTSOE = "entsoe_load"
COLUMNAS   = list(INDICADORES) + [COL_ENTSOE]

# Su ausencia SI indica fallo: hay que reintentar el dia.
CRITICOS = set(COLUMNAS)

# Una hora de tolerancia en todas: el ultimo domingo de octubre ESIOS no
# publica una de las dos 02:00. Verificado en 2020-2025 sin excepcion.
TOLERANCIA_CRITICOS = {c: 1 for c in CRITICOS}
TOLERANCIA_DEFECTO = 0


def setup_logger(run_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"load_inter_daily_{run_date}.log"
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


# ── ENTSO-E ───────────────────────────────────────────────────────────────────

def fetch_entsoe_load(client, dia: date, horas: set, log) -> dict:
    """
    Actual Total Load de la zona ES para un dia.
    Resample horario ANTES de filtrar: los datos son cuarto-horarios desde
    oct-2025 (MTU15) y filtrar primero descarta :15/:30/:45, dejando una sola
    muestra por hora en vez del promedio real.
    Media y no suma porque es POTENCIA (MW).
    """
    ts_ini = pd.Timestamp(str(dia - timedelta(days=1)), tz="Europe/Madrid")
    ts_fin = pd.Timestamp(str(dia + timedelta(days=1)), tz="Europe/Madrid")

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            df = client.query_load(COUNTRY, start=ts_ini, end=ts_fin)
            serie = df["Actual Load"]
            if serie.empty:
                return {}
            serie = serie.resample("h").mean()
            if serie.index.tzinfo is None:
                serie.index = serie.index.tz_localize("UTC")
            serie.index = serie.index.tz_convert("UTC")
            return {ts.to_pydatetime(): float(v)
                    for ts, v in serie.items()
                    if ts.to_pydatetime() in horas and pd.notna(v)}
        except Exception as e:
            if intento < MAX_REINTENTOS:
                time.sleep(3 * intento)
            else:
                log.warning(f"    {COL_ENTSOE}: {str(e)[:70]}")
    return {}


# ── BD ────────────────────────────────────────────────────────────────────────

def estado_dia(conn, horas: set):
    """(horas_existentes, {columna: n_huecos}) en una sola pasada."""
    ini, fin = min(horas), max(horas)
    conteos = ", ".join(f"count({c})" for c in COLUMNAS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        fila = cur.fetchone()
        cur.execute(f"SELECT datetime FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        existentes = {r[0] for r in cur.fetchall()}

    n = len(horas)
    total = fila[0]
    huecos = {c: n - fila[i + 1] for i, c in enumerate(COLUMNAS)
              if n - fila[i + 1] > 0}

    # Si el dia esta vacio hay que pedir TODO. Con MODO_RELLENAR y la tabla
    # sin filas, huecos ya sale completo, pero se deja explicito: es el bug
    # que aparecio en el pipeline de PBF, donde en la primera carga de un dia
    # solo se pedian los criticos y el resto no se descargaba nunca.
    if total == 0:
        huecos = {c: n for c in COLUMNAS}
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
                SET {col} = v.valor
                FROM (VALUES %s) AS v(ts, valor)
                WHERE t.datetime = v.ts{filtro}
            """, registros, template="(%s, %s::numeric)", page_size=500)
            upd += cur.rowcount
        conn.commit()
    return ins, upd


def verificar_dia(conn, dia: date, horas: set, log) -> bool:
    """
    True si el dia se da por bueno. Un critico por debajo de su tolerancia lo
    marca como incompleto. Los dos dias del apagon (28-29 abr 2025) fallaran
    siempre en entsoe_load: es correcto, no hay dato que recuperar.
    """
    ini, fin = min(horas), max(horas)
    conteos = ", ".join(f"count({c})" for c in COLUMNAS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        fila = cur.fetchone()

    n = len(horas)
    fallos, avisos = [], []
    for i, c in enumerate(COLUMNAS):
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


def procesar_dia(conn, dia: date, headers, client, fuentes, log) -> bool:
    horas = expected_hours_utc(dia)
    n_esperadas = len(horas)
    marca = ""
    if n_esperadas == 23:
        marca = "  (cambio de hora: dia de 23h)"
    elif n_esperadas == 25:
        marca = "  (cambio de hora: dia de 25h)"

    existentes, huecos = estado_dia(conn, horas)
    log.info(f"  {dia}: {n_esperadas} horas esperadas{marca}")

    pedir = COLUMNAS if not MODO_RELLENAR else list(huecos)
    pedir_esios  = [c for c in pedir if c in INDICADORES and "esios" in fuentes]
    pedir_entsoe = [c for c in pedir if c == COL_ENTSOE and "entsoe" in fuentes]

    if not pedir_esios and not pedir_entsoe:
        log.info("    ya completo, no se pide nada")
        return True

    datos = {}
    for col in pedir_esios:
        d = fetch_indicador(col, dia, headers, log)
        time.sleep(PAUSA_SEC)
        if d:
            datos[col] = d

    if pedir_entsoe:
        d = fetch_entsoe_load(client, dia, horas, log)
        time.sleep(PAUSA_API_SEC)
        if d:
            datos[COL_ENTSOE] = d

    ins, upd = escribir_dia(conn, horas, datos, existentes)
    n_pedidos = len(pedir_esios) + len(pedir_entsoe)
    log.info(f"    {ins} ins / {upd} upd sobre {n_pedidos} indicadores")
    return verificar_dia(conn, dia, horas, log)


def main():
    p = argparse.ArgumentParser(description="Carga diaria unificada de load_inter")
    p.add_argument("--dia", help="Dia concreto YYYY-MM-DD")
    p.add_argument("--dias", type=int, default=DIAS_ATRAS,
                   help="Ultimos N dias hasta D-1 (por defecto 1)")
    p.add_argument("--recalculo", action="store_true",
                   help="Sobrescribe en vez de rellenar solo los NULL")
    p.add_argument("--solo", choices=["esios", "entsoe"],
                   help="Cargar de una sola fuente")
    args = p.parse_args()

    global MODO_RELLENAR
    if args.recalculo:
        MODO_RELLENAR = False

    fuentes = {args.solo} if args.solo else {"esios", "entsoe"}

    hoy = datetime.now(TZ_SPAIN).date()
    if args.dia:
        dias = [date.fromisoformat(args.dia)]
    else:
        dias = [hoy - timedelta(days=k) for k in range(args.dias, 0, -1)]

    log = setup_logger(hoy)
    creds, db_config = load_config()
    headers = get_headers()
    entsoe_token = json.load(open(CREDS_PATH))["entsoe_token"]
    client = (EntsoePandasClient(api_key=entsoe_token)
              if "entsoe" in fuentes else None)
    conn = psycopg2.connect(**db_config)

    n_sum = len(COLUMNAS_SUMA)
    log.info("=" * 74)
    log.info("Carga diaria load_inter (ESIOS + ENTSO-E)")
    log.info(f"  Dias        : {dias[0]} a {dias[-1]}  ({len(dias)})")
    log.info(f"  Fuentes     : {', '.join(sorted(fuentes))}")
    log.info(f"  Columnas    : {len(COLUMNAS)}  |  geo_id={GEO_PENINSULA}")
    log.info(f"  Agregacion  : {n_sum} con sum (MWh), "
             f"{len(INDICADORES)-n_sum} con average (MW), "
             f"1 con resample mean (ENTSO-E)")
    log.info(f"  Modo        : {'RELLENAR NULLs' if MODO_RELLENAR else 'SOBRESCRIBIR'}")
    log.info("=" * 74)

    t0 = time.time()
    ok, ko = [], []
    for d in dias:
        try:
            (ok if procesar_dia(conn, d, headers, client, fuentes, log)
             else ko).append(d)
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
