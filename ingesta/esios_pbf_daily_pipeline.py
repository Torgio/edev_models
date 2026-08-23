"""
TFM Energia UCM — ESIOS PBF Daily Pipeline v1
Descarga el Programa Diario Base de Funcionamiento (PBF) de ESIOS
y lo carga en tres tablas:
    esios_pbf_gen        -> generacion programada por tecnologia (20 cols)
    esios_pbf_load_inter -> demanda programada e interconexiones (11 cols)
    esios_pbf_bilateral  -> programa bilateral (14 cols)

QUE ES EL PBF
El P.O. 3.1 (BOE num 313, 30-dic-2019) lo llama PDBF; ESIOS lo abrevia PBF.
Es el mismo programa. Segun el apartado 4.1:

    PDBF = PDBC + contratos bilaterales nominados

de modo que restando el bilateral al PBF se reconstruye el PDBC, que es el
resultado limpio de la casacion del mercado diario y NO se publica.

AGREGACION — verificado 06/08/2026 con ingesta/_tests/:
  - Los indicadores del PBF son CUARTO-HORARIOS (96 valores/dia), pese a que
    sus descripciones oficiales dicen "con desglose horario".
  - La agregacion correcta es time_agg=SUM, no average. El P.O. 3.1
    (apartado 3) establece que "los programas de energia corresponderan a
    valores de MWh". Comprobado con el 10258: sum -> 30.358 MWh/h (coherente
    con ~30 GW de demanda peninsular), average -> 7.589 (imposible).
  - OJO: esto es lo CONTRARIO que en esios_forecast_da, donde los indicadores
    son POTENCIA (MW) y se usa time_agg=average.
  - Las horas con menos de 4 muestras nativas NO estan infravaloradas: son
    horas sin programa que ESIOS no publica (el bombeo solo consume en valle,
    el ciclo combinado no siempre esta acoplado, la FV arranca a mitad de hora
    al amanecer). Los cuartos ausentes valen 0 y no aportan a la suma.

FRONTERA DE INFORMACION — el PBF se publica a las 13:45 del dia D-1, es decir
DESPUES del cierre del mercado diario (12:00). No es un predictor del precio:
es consecuencia de la misma casacion que lo fija. Usos legitimos:
  - con retardo (pbf_lag1, pbf_lag7)
  - analisis de desviacion programa vs medida real (esios_marketdata)
  - reconstruccion del PDBC restando el bilateral

INDICADORES DESCARTADOS tras verificar 5 fechas de 2020 a 2026:
  - 429 y 431 (bilateral de ciclo combinado y gas natural): cero datos en las
    5 fechas. IDs en desuso; el bilateral de gas va por el 437 (cogeneracion).
  - 422, 423, 445, 430, 435, 441, 442, 455, 459: cero datos en el bilateral.
  - 425, 426, 427, 428 y 10075 (Anexo II RD 134/2010): mecanismo de garantia
    de suministro derogado.
  - 433 (eolica marina) y 436 (oceano y geotermica): no existen en España.
  - 355, 356, 357 y 10196 (correcciones y ajuste de programas): sus propias
    descripciones dicen "Solo tiene valores para el P48".

Cron job (servidor) — el PBF sale a las 13:45 hora española:
    30 14 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_pbf_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_esios_pbf.log 2>&1
    (con CRON_TZ=Europe/Madrid antes de la linea)
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import psycopg2
from psycopg2.extras import execute_values

from config import load_config
from refresh_pdbc import refrescar_pdbc

# ── Configuracion ──────────────────────────────────────────────────────────────

# Sin bucle de reintentos: el PBF es firme una vez publicado a las 13:45.
# Si falta algo, lo recoge la revision del dia siguiente.
DIAS_REVISION       = 30   # el PBF es firme: una pasada al dia y revision de un mes atras
TIMEOUT_SEC         = 60
PAUSE_API_SEC       = 0.4
TOLERANCIA          = 0.01
TZ_SPAIN            = ZoneInfo("Europe/Madrid")

# Si True, sobrescribe los valores ya cargados cuando difieren de la API.
# El PBF es firme una vez publicado, asi que por defecto solo rellena NULLs.
MODO_ACTUALIZAR = False

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.esios.ree.es/indicators"

# SUM y no AVERAGE: los programas son MWh (P.O. 3.1, apartado 3).
TIME_AGG = "sum"

# ── Indicadores por tabla ──────────────────────────────────────────────────────
# Un diccionario por tabla: esios_pbf_gen y esios_pbf_bilateral comparten
# nombres de tecnologia, y el prefijo bil_ es lo que las distingue.

IND_GEN = {
    10073: "wind_mw",                # PBF Eolica (agregado terrestre+marina)
    14:    "solar_pv_mw",            # PBF Solar fotovoltaica
    15:    "solar_thermal_mw",       # PBF Solar termica
    2:     "hydro_no_ugh_mw",        # PBF Hidraulica no UGH (no despachable)
    1:     "hydro_ugh_mw",           # PBF Hidraulica UGH (incluye bombeo mixto)
    10064: "total_hydro_mw",         # PBF UGH + no UGH
    3:     "pumping_gen_mw",         # PBF Turbinacion bombeo
    25:    "pumping_cons_mw",        # PBF Consumo bombeo (viene NEGATIVO)
    21:    "biomass_mw",
    22:    "biogas_mw",
    10095: "waste_mw",               # PBF Residuos (agregado)
    10074: "other_renew_mw",         # PBF otras renovables (agregado)
    4:     "nuclear_mw",
    9:     "ccgt_mw",                # PBF Ciclo combinado
    10086: "cogen_mw",               # PBF Cogeneracion (agregado)
    10167: "coal_mw",                # PBF Carbon (cerrado desde 2021)
    10077: "fuel_gas_mw",            # PBF Fuel-Gas (residual)
    2132:  "hybrid_mw",              # PBF Hibridacion (figura reciente)
    10258: "total_gen_mw",           # PBF total — control de calidad
    462:   "unavailable_power_mw",   # Potencia indisponible en PBF
}

IND_LOAD = {
    351:   "demand_free_market_mw",  # Comercializadores mercado libre
    352:   "demand_reference_mw",    # Comercializadores de referencia (COR)
    353:   "demand_direct_mw",       # Consumos directos en mercado
    354:   "demand_aux_mw",          # Consumo de servicios auxiliares
    10141: "total_demand_mw",        # Demanda programada PBF total
    10104: "net_flow_fr_mw",         # Saldo Francia
    10113: "net_flow_pt_mw",         # Saldo Portugal
    10122: "net_flow_ma_mw",         # Saldo Marruecos
    10131: "net_flow_ad_mw",         # Saldo Andorra
    10186: "total_net_flow_mw",      # Saldo total interconexiones
    26:    "baleares_mw",            # Enlace Baleares
}

IND_BIL = {
    # Tecnologias (verificadas en el desglose oficial del 10235)
    421:   "bil_hydro_ugh_mw",        #  8,0%
    422:   "bil_hydro_no_ugh_mw",     #  0,5%
    424:   "bil_nuclear_mw",          # 19,8%
    432:   "bil_wind_onshore_mw",     #  9,6%
    434:   "bil_solar_pv_mw",         # 20,3%
    435:   "bil_solar_thermal_mw",    #  0,1%
    437:   "bil_cogen_mw",            #  0,8%
    438:   "bil_petro_coal_mw",       #  0,0%
    441:   "bil_biomass_mw",          #  0,0%
    442:   "bil_biogas_mw",           #  0,0%
    2142:  "bil_hybrid_mw",           #  1,2%
    10233: "bil_coal_mw",             # solo hasta 2021
    # Intermediacion (40% del total, NO es generacion)
    454:   "bil_retail_free_sales_mw",# 13,0%
    455:   "bil_generic_sales_mw",    # 26,8%
    10235: "bil_total_sales_mw",
    # Lado comprador
    456:   "bil_retail_free_buy_mw",
    457:   "bil_retail_last_resort_mw",
    458:   "bil_direct_consumer_mw",
    459:   "bil_generic_buy_mw",
    10236: "bil_total_purchases_mw",
}

TABLAS = {
    "esios_pbf_gen":        IND_GEN,
    "esios_pbf_load_inter": IND_LOAD,
    "esios_pbf_bilateral":  IND_BIL,
}

# Criticos: si faltan, el dia no se considera completo y se reintenta.
CRITICOS = {
    "esios_pbf_gen":        {"total_gen_mw", "nuclear_mw", "solar_pv_mw", "wind_mw"},
    "esios_pbf_load_inter": {"total_demand_mw"},
    "esios_pbf_bilateral":  {"bil_total_sales_mw", "bil_total_purchases_mw"},
}

# Esporadicos: null es un resultado valido, no se reintenta por ellos.
# Verificado con 5 fechas de 2020 a 2026.
ESPORADICOS = {
    # generacion
    # ccgt_mw: el ciclo combinado no siempre esta acoplado, asi que hay horas
    # sin programa (10 de 24 el 04/08/2026). Sin estar aqui se marcaba como
    # null recuperable y bloqueaba la revision de dias anteriores.
    "ccgt_mw",
    "coal_mw", "fuel_gas_mw", "hybrid_mw",
    "pumping_gen_mw", "pumping_cons_mw",
    "biomass_mw", "biogas_mw", "waste_mw", "other_renew_mw",
    "solar_thermal_mw",
    # demanda e interconexiones
    "net_flow_ma_mw", "net_flow_ad_mw", "baleares_mw", "demand_aux_mw",
    # bilateral
    "bil_coal_mw",            # cerrado desde 2021
    "bil_solar_pv_mw",        # nulo de noche
    "bil_solar_thermal_mw",   # 0,1% del total
    "bil_petro_coal_mw",      # 0,0%
    "bil_biomass_mw",         # 0,0%
    "bil_biogas_mw",          # 0,0%
    "bil_cogen_mw",
    "bil_hybrid_mw",
    "bil_retail_last_resort_mw",
    "bil_generic_buy_mw",     # 114 MWh/h, puede faltar
}

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"esios_pbf_pipeline_{target_date}.log"
    logger = logging.getLogger(f"esios_pbf_{target_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# ── Helpers UTC / hora española ────────────────────────────────────────────────

def expected_hours_utc(target: date) -> set:
    """
    Timestamps UTC esperados para el dia target en hora española.
    Soporta 23h (cambio a verano), 24h y 25h (cambio a invierno).
    """
    start = datetime(target.year, target.month, target.day, 0, 0, tzinfo=TZ_SPAIN)
    end   = datetime(target.year, target.month, target.day, 23, 0, tzinfo=TZ_SPAIN)
    start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    horas, cur = set(), start_utc
    while cur <= end_utc:
        horas.add(cur)
        cur += timedelta(hours=1)
    return horas


def day_range_utc(target: date) -> tuple[datetime, datetime]:
    start = datetime(target.year, target.month, target.day, 0, 0, tzinfo=TZ_SPAIN)
    end   = datetime(target.year, target.month, target.day, 23, 0, tzinfo=TZ_SPAIN)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

# ── ESIOS API ──────────────────────────────────────────────────────────────────

def get_headers(creds: dict) -> dict:
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


def fetch_indicator_for_day(ind_id: int, target: date, headers: dict) -> dict:
    """
    Descarga un indicador para el dia target en hora española.
    Pide target-1 a target+1 en UTC para cubrir el desfase CET/CEST y filtra
    despues por dia español.
    """
    start_utc = target - timedelta(days=1)
    end_utc   = target + timedelta(days=1)

    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={start_utc}T00:00:00"
           f"&end_date={end_utc}T23:59:59"
           f"&time_trunc=hour"
           f"&time_agg={TIME_AGG}")

    for intento in range(1, 4):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            r.raise_for_status()
            valores = r.json().get("indicator", {}).get("values", [])
            out = {}
            for v in valores:
                dt_str = v.get("datetime_utc") or v.get("datetime")
                val    = v.get("value")
                if not dt_str or val is None:
                    continue
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.astimezone(TZ_SPAIN).date() == target:
                        out[dt] = float(val)
                except Exception:
                    pass
            return out
        except Exception as e:
            if intento < 3:
                time.sleep(5 * intento)
            else:
                return {}
    return {}

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_tabla_status(conn, tabla: str, cols: list, criticos: set,
                     target: date, n_expected: int, log) -> dict:
    """Estado de una tabla para el dia: horas presentes, criticos y nulls."""
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {tabla} "
                    f"WHERE datetime >= %s AND datetime <= %s", (start_utc, end_utc))
        total = cur.fetchone()[0]

        # Una sola consulta con FILTER por columna, en vez de una por columna
        filtros = ", ".join(
            [f"COUNT(*) FILTER (WHERE {c} IS NULL) AS n_{i}" for i, c in enumerate(cols)])
        cur.execute(f"SELECT {filtros} FROM {tabla} "
                    f"WHERE datetime >= %s AND datetime <= %s", (start_utc, end_utc))
        row = cur.fetchone() or ()

    nulls = {cols[i]: row[i] for i in range(len(cols)) if i < len(row) and row[i]}

    criticos_ok = True
    for c in criticos:
        faltan = nulls.get(c, 0) + max(0, n_expected - total)
        if faltan > 0:
            criticos_ok = False
            log.warning(f"    [CRITICO] {tabla}.{c}: faltan {faltan}/{n_expected}h")

    recuperables = {c: n for c, n in nulls.items()
                    if c not in ESPORADICOS and c not in criticos}
    esporadicos  = {c: n for c, n in nulls.items() if c in ESPORADICOS}

    return {"total": total, "criticos_ok": criticos_ok,
            "recuperables": recuperables, "esporadicos": esporadicos}


def get_day_status(conn, target: date, log) -> dict:
    """Estado combinado del dia en las tres tablas."""
    n_expected = len(expected_hours_utc(target))
    estados = {}

    for tabla, inds in TABLAS.items():
        estados[tabla] = get_tabla_status(
            conn, tabla, list(inds.values()), CRITICOS[tabla],
            target, n_expected, log)

    criticos_ok  = all(e["criticos_ok"] for e in estados.values())
    completo     = all(e["total"] >= n_expected for e in estados.values()) and criticos_ok
    recuperables = {k: v for e in estados.values() for k, v in e["recuperables"].items()}
    esporadicos  = {k: v for e in estados.values() for k, v in e["esporadicos"].items()}

    resumen = " ".join(f"{t.replace('esios_pbf_','')}={e['total']}h"
                       for t, e in estados.items())
    log.info(f"  [{target}] {resumen}/{n_expected}h | "
             f"criticos={'✅' if criticos_ok else '❌'} | "
             f"{'✅ COMPLETO' if completo else '⚠️ incompleto'}")
    if recuperables:
        log.warning(f"    [nulls recuperables] {list(recuperables.keys())}")
    if esporadicos:
        log.debug(f"    [nulls esporadicos — OK] {list(esporadicos.keys())}")

    return {"n_expected": n_expected, "estados": estados,
            "criticos_ok": criticos_ok, "es_completo": completo,
            "recuperables": recuperables, "esporadicos": esporadicos}


def insert_new(conn, tabla: str, col: str, records: list) -> int:
    if not records:
        return 0
    with conn.cursor() as cur:
        execute_values(cur,
            f"INSERT INTO {tabla} (datetime, {col}) VALUES %s "
            f"ON CONFLICT (datetime) DO NOTHING",
            records, page_size=500)
    conn.commit()
    return len(records)


def update_col(conn, tabla: str, col: str, records: list, sobrescribir: bool) -> int:
    """
    Rellena NULLs y, si sobrescribir=True, tambien corrige valores que difieran.
    En lote: 1 SELECT + 1 UPDATE, no 2 consultas por celda.
    """
    if not records:
        return 0

    ts_list = [ts for ts, _ in records]
    with conn.cursor() as cur:
        cur.execute(f"SELECT datetime, {col} FROM {tabla} WHERE datetime = ANY(%s)",
                    (ts_list,))
        actuales = dict(cur.fetchall())

        cambios = []
        for ts, valor in records:
            if ts not in actuales:
                continue
            act = actuales[ts]
            if act is None:
                cambios.append((ts, valor))
            elif sobrescribir and abs(float(act) - valor) > TOLERANCIA:
                cambios.append((ts, valor))

        if not cambios:
            return 0

        execute_values(cur, f"""
            UPDATE {tabla} AS t
            SET {col} = v.valor, updated_at = now()
            FROM (VALUES %s) AS v(ts, valor)
            WHERE t.datetime = v.ts
        """, cambios, template="(%s, %s::numeric)", page_size=500)

    conn.commit()
    return len(cambios)


def cargar_dia(conn, target: date, headers: dict, log) -> tuple[int, int]:
    """Descarga y escribe los indicadores que faltan, en las tres tablas."""
    expected = expected_hours_utc(target)
    total_ins = total_upd = 0

    for tabla, inds in TABLAS.items():
        cols = list(inds.values())
        start_utc, end_utc = day_range_utc(target)

        with conn.cursor() as cur:
            cur.execute(f"SELECT datetime FROM {tabla} "
                        f"WHERE datetime >= %s AND datetime <= %s", (start_utc, end_utc))
            existing = {r[0] for r in cur.fetchall()}

        ins_t = upd_t = 0
        for ind_id, col in inds.items():
            # ¿hace falta descargar esta columna?
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {tabla} "
                            f"WHERE datetime >= %s AND datetime <= %s AND {col} IS NULL",
                            (start_utc, end_utc))
                n_nulls = cur.fetchone()[0]

            faltan_horas = expected - existing
            if not faltan_horas and not n_nulls and not MODO_ACTUALIZAR:
                continue

            datos = fetch_indicator_for_day(ind_id, target, headers)
            time.sleep(PAUSE_API_SEC)
            if not datos:
                if col not in ESPORADICOS:
                    log.debug(f"    {tabla}.{col} (id {ind_id}): sin datos de la API")
                continue

            nuevos      = [(ts, v) for ts, v in datos.items() if ts in faltan_horas]
            existentes  = [(ts, v) for ts, v in datos.items()
                           if ts in existing and ts in expected]

            if nuevos:
                n = insert_new(conn, tabla, col, nuevos)
                ins_t += n
                existing |= {ts for ts, _ in nuevos}
            if existentes:
                upd_t += update_col(conn, tabla, col, existentes, MODO_ACTUALIZAR)

        if ins_t or upd_t:
            log.info(f"    {tabla}: {ins_t} insert, {upd_t} update")
        total_ins += ins_t
        total_upd += upd_t

    return total_ins, total_upd


def log_pipeline_db(conn, target, intento, ins, upd, estado, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (f"esios_pbf_{target}_intento_{intento}", target, target,
                  ins + upd, estado, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()

# ── Revision de dias anteriores ───────────────────────────────────────────────

def revisar_dias_anteriores(headers: dict, db_config: dict, log):
    hoy = date.today()
    log.info(f"\n--- Revision ultimos {DIAS_REVISION} dias ---")
    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD: {e}")
        return

    for i in range(1, DIAS_REVISION + 1):
        dia = hoy - timedelta(days=i)
        status = get_day_status(conn, dia, log)
        if status["es_completo"] and not status["recuperables"]:
            continue
        log.info(f"  [{dia}] Rellenando huecos...")
        ins, upd = cargar_dia(conn, dia, headers, log)
        log.info(f"  [{dia}] {ins} insert, {upd} update")

    conn.close()
    log.info("--- Revision completada ---\n")

# ── Main ───────────────────────────────────────────────────────────────────────

def run(target: date | None = None):
    hoy = date.today()
    # El PBF del dia D+1 se publica a las 13:45 del dia D
    objetivo = target or (hoy + timedelta(days=1))
    log = setup_logger(hoy)

    n_ind = sum(len(v) for v in TABLAS.values())

    log.info("=" * 62)
    log.info(f"ESIOS PBF Pipeline diario v1 — {hoy}")
    log.info(f"Programa objetivo: {objetivo} (D+1, se publica 13:45 de D)")
    log.info(f"Tablas: {', '.join(TABLAS)}")
    log.info(f"Indicadores: {n_ind}  |  agregacion: time_trunc=hour & time_agg={TIME_AGG}")
    log.info(f"Modo actualizar: {MODO_ACTUALIZAR}")
    log.info(f"Revision: ultimos {DIAS_REVISION} dias")
    log.info(f"UTC/hora española | 23/24/25h soportado")
    log.info("=" * 62)

    try:
        _, db_config = load_config()
        creds   = json.load(open(Path(__file__).parent / "credentials.json"))
        headers = get_headers(creds)
    except Exception as e:
        log.error(f"Error credenciales: {e}")
        return

    # ── PASO 1: una sola pasada, sin reintentos ──
    # El PBF es firme una vez publicado a las 13:45. Si falta algo, lo recoge
    # la revision del dia siguiente, no tiene sentido esperar horas.
    log.info(f"\n=== PASO 1: PBF de {objetivo} ===")
    conn = None
    try:
        conn = psycopg2.connect(**db_config)
        status = get_day_status(conn, objetivo, log)
        if status["es_completo"] and not status["recuperables"]:
            log.info(f"  ✅ Dia {objetivo} ya completo")
        else:
            ins, upd = cargar_dia(conn, objetivo, headers, log)
            get_day_status(conn, objetivo, log)
            log.info(f"  {ins} insert, {upd} update")
    except Exception as e:
        log.error(f"  Error: {e}")
    finally:
        if conn:
            conn.close()

    # ── PASO 2: rellenar huecos de dias anteriores ──
    log.info(f"\n=== PASO 2: Revision ultimos {DIAS_REVISION} dias ===")
    revisar_dias_anteriores(headers, db_config, log)

    # esios_pdbc_gen = PDBF menos bilaterales. Se refresca aqui para que
    # no dependa de un cron aparte que se pueda olvidar.
    import psycopg2 as _pg
    _c = _pg.connect(**db_config)
    # ESIOS no publica punto cuando el programa es cero, y el INSERT va
    # columna a columna: la columna queda NULL. Para las termicas esporadicas
    # eso significa "no acoplado", es decir 0 MW, no dato ausente.
    with _c.cursor() as _cur:
        _cur.execute("""
            UPDATE esios_pbf_gen
            SET ccgt_mw = COALESCE(ccgt_mw,0), coal_mw = COALESCE(coal_mw,0),
                fuel_gas_mw = COALESCE(fuel_gas_mw,0),
                hybrid_mw = COALESCE(hybrid_mw,0)
            WHERE datetime > now() - interval '10 days'
              AND (ccgt_mw IS NULL OR coal_mw IS NULL
                   OR fuel_gas_mw IS NULL OR hybrid_mw IS NULL)
        """)
        if _cur.rowcount:
            log.info(f"  termicas esporadicas: {_cur.rowcount} nulos a cero")
    _c.commit()

    try:
        refrescar_pdbc(log, _c)
    finally:
        _c.close()

    log.info("\nPipeline ESIOS PBF finalizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESIOS PBF daily pipeline")
    parser.add_argument("--fecha", help="Dia de programa concreto YYYY-MM-DD")
    args = parser.parse_args()
    run(date.fromisoformat(args.fecha) if args.fecha else None)