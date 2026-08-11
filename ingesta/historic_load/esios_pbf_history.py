"""
TFM Energia UCM — ESIOS PBF Historic Loader
Carga historica del Programa Diario Base de Funcionamiento (PBF) en las tres
tablas del bloque:
    esios_pbf_gen        -> generacion programada por tecnologia (20 indicadores)
    esios_pbf_load_inter -> demanda programada e interconexiones (11)
    esios_pbf_bilateral  -> programa bilateral (20)

Reutiliza IND_GEN / IND_LOAD / IND_BIL identicos al pipeline diario
(esios_pbf_daily_pipeline.py), asi que ambos escriben exactamente lo mismo.

AGREGACION — time_agg=SUM, verificado 11/08/2026 con tests propios:
  Los indicadores del PBF son CUARTO-HORARIOS y en MWh, pese a que sus
  descripciones oficiales dicen "con desglose horario". El P.O. 3.1 (BOE 313,
  30-dic-2019, apartado 3) establece que "los programas de energia
  corresponderan a valores de MWh". Comprobado con el 10258: sum -> 30.358
  MWh/h (coherente con ~30 GW de demanda peninsular), average -> 7.589
  (imposible).
  OJO: esto es lo CONTRARIO que esios_forecast_da, donde los indicadores son
  POTENCIA (MW) y se usa time_agg=average.
  Las horas con menos de 4 muestras nativas NO estan infravaloradas: son horas
  sin programa que ESIOS no publica (bombeo solo en valle, ciclo combinado no
  siempre acoplado, FV arranca a mitad de hora al amanecer). Los cuartos
  ausentes valen 0 y no aportan a la suma.

DESFASE UTC / HORA ESPAÑOLA — se pide cada chunk con MARGEN_DIAS de margen a
ambos lados. Las 00:00 hora española son las 22:00 UTC del dia anterior en
verano (CEST, UTC+2), y sin el margen esas horas frontera quedaban fuera del
set y no se cargaban (el fallo que en esios_forecast_da se veia como "166 upd
de 168 API rows"). El margen se usa solo para ACTUALIZAR; para INSERTAR se usa
el rango exacto de dias españoles, para no crear filas fuera del periodo pedido.

RENDIMIENTO — chunks semanales y upsert en lote (1 SELECT + 1 UPDATE por
columna y chunk, no 2 consultas por celda). Con 51 indicadores y ~2.400 dias:
al ritmo del pipeline diario seria ~30 horas; asi baja a 4-5.

Uso:
    python esios_pbf_history.py                 # rango de la configuracion
    python esios_pbf_history.py --desde 2024-01-01 --hasta 2024-12-31
Conviene lanzarlo con nohup:
    nohup ~/tfm-env/bin/python -u .../esios_pbf_history.py > log 2>&1 &
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

# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI              ║
# ╚══════════════════════════════════════════════════════════════╝

START_DATE = "2020-01-01"
END_DATE   = "2020-01-01"

MODO_RECALCULO = False   # False = solo inserta filas nuevas y rellena NULLs
                         # True  = descarga siempre y sobrescribe si el valor
                         #         difiere (para corregir datos ya cargados)

CHUNK_DAYS  = 7
PAUSE_SEC   = 0.4
TIMEOUT_SEC = 60
MAX_RETRIES = 3
TOLERANCIA  = 0.01
MARGEN_DIAS = 1

# ╚══════════════════════════════════════════════════════════════╝

BASE_URL = "https://api.esios.ree.es/indicators"
TZ_SPAIN = ZoneInfo("Europe/Madrid")

# SUM y no AVERAGE: los programas son MWh (P.O. 3.1, apartado 3).
TIME_AGG = "sum"

# ── Indicadores por tabla (identicos al pipeline diario) ──────────────────────

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
    # Lado vendedor — tecnologias del desglose oficial del 10235.
    # Verificado 11/08/2026: los 13 componentes suman exactamente el total
    # (residuo 0,00 en las 24 horas), asi que son exhaustivos y
    # PDBC_tecnologia = PBF_tecnologia - bilateral_tecnologia es exacto.
    421:   "bil_hydro_ugh_mw",         #  8,0% del total
    422:   "bil_hydro_no_ugh_mw",      #  0,5%
    424:   "bil_nuclear_mw",           # 19,8%
    432:   "bil_wind_onshore_mw",      #  9,6%
    434:   "bil_solar_pv_mw",          # 20,3%
    435:   "bil_solar_thermal_mw",     #  0,1%
    437:   "bil_cogen_mw",             #  0,8% (desde 2023)
    438:   "bil_petro_coal_mw",        #  0,0%
    441:   "bil_biomass_mw",           #  0,0%
    442:   "bil_biogas_mw",            #  0,0%
    2132 + 10: "bil_hybrid_mw",        # 2142 — PBF Hibridacion bilateral
    10233: "bil_coal_mw",              # solo hasta 2021 (cierre del carbon)
    # Intermediacion — 40% del total, NO es generacion: es energia que ya se
    # conto al producirse. No debe restarse a ninguna tecnologia.
    454:   "bil_retail_free_sales_mw", # 13,0%
    455:   "bil_generic_sales_mw",     # 26,8%
    10235: "bil_total_sales_mw",
    # Lado comprador
    456:   "bil_retail_free_buy_mw",
    457:   "bil_retail_last_resort_mw",   # desde 2023
    458:   "bil_direct_consumer_mw",
    459:   "bil_generic_buy_mw",
    10236: "bil_total_purchases_mw",
}

TABLAS = {
    "esios_pbf_gen":        IND_GEN,
    "esios_pbf_load_inter": IND_LOAD,
    "esios_pbf_bilateral":  IND_BIL,
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("esios_pbf_history")

# ── Helpers de fechas ─────────────────────────────────────────────────────────

def chunk_bounds_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """Rango UTC ampliado +-MARGEN_DIAS. Para UPDATE (cubre horas frontera)."""
    ini = (datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
           - timedelta(days=MARGEN_DIAS))
    fin = (datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc)
           + timedelta(days=MARGEN_DIAS))
    return ini, fin


def expected_hours(start: date, end: date) -> set:
    """Horas UTC del chunk con margen. Para decidir que se puede ACTUALIZAR."""
    ini, fin = chunk_bounds_utc(start, end)
    horas, d = set(), ini
    while d <= fin:
        horas.add(d)
        d += timedelta(hours=1)
    return horas


def target_hours(start: date, end: date) -> set:
    """
    Horas UTC que corresponden EXACTAMENTE a los dias españoles del chunk,
    sin margen. Para decidir que filas se pueden CREAR: si se usara el rango
    ampliado, el primer chunk crearia filas del dia anterior al periodo pedido
    con una sola columna rellena. Soporta dias de 23h/24h/25h.
    """
    horas, d = set(), start
    while d <= end:
        h0  = datetime(d.year, d.month, d.day, 0, tzinfo=TZ_SPAIN).astimezone(timezone.utc)
        h23 = datetime(d.year, d.month, d.day, 23, tzinfo=TZ_SPAIN).astimezone(timezone.utc)
        t = h0
        while t <= h23:
            horas.add(t)
            t += timedelta(hours=1)
        d += timedelta(days=1)
    return horas


def bloques(start: date, end: date, dias: int):
    out, cur = [], start
    while cur <= end:
        fin = min(cur + timedelta(days=dias - 1), end)
        out.append((cur, fin))
        cur = fin + timedelta(days=1)
    return out

# ── ESIOS API ─────────────────────────────────────────────────────────────────

def get_headers(creds: dict) -> dict:
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


def fetch_chunk(ind_id: int, start: date, end: date, headers: dict) -> dict:
    """Descarga un indicador para el chunk, con margen y reintentos."""
    ini = start - timedelta(days=MARGEN_DIAS)
    fin = end + timedelta(days=MARGEN_DIAS)

    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={ini}T00:00:00"
           f"&end_date={fin}T23:59:59"
           f"&time_trunc=hour"
           f"&time_agg={TIME_AGG}")

    for intento in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            r.raise_for_status()
            out = {}
            for v in r.json().get("indicator", {}).get("values", []):
                dt_str = v.get("datetime_utc") or v.get("datetime")
                val    = v.get("value")
                if not dt_str or val is None:
                    continue
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    out[dt] = float(val)
                except Exception:
                    pass
            return out
        except Exception as e:
            if intento < MAX_RETRIES:
                time.sleep(5 * intento)
            else:
                log.warning(f"    id {ind_id}: {str(e)[:70]}")
    return {}

# ── BD ────────────────────────────────────────────────────────────────────────

def estado_chunk(conn, tabla: str, col: str, start: date, end: date) -> tuple[set, set]:
    """Devuelve (horas_existentes, horas_con_null_en_col) del chunk ampliado."""
    ini, fin = chunk_bounds_utc(start, end)
    with conn.cursor() as cur:
        cur.execute(f"SELECT datetime FROM {tabla} "
                    f"WHERE datetime >= %s AND datetime <= %s", (ini, fin))
        existentes = {r[0] for r in cur.fetchall()}
        cur.execute(f"SELECT datetime FROM {tabla} "
                    f"WHERE datetime >= %s AND datetime <= %s AND {col} IS NULL",
                    (ini, fin))
        con_nulls = {r[0] for r in cur.fetchall()}
    return existentes, con_nulls


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


def upsert_col(conn, tabla: str, col: str, records: list) -> int:
    """
    Rellena NULLs y, con MODO_RECALCULO, sobrescribe lo que difiera.
    En lote: 1 SELECT + 1 UPDATE por columna y chunk. Con ~70 ms de latencia
    al servidor, la version por celda tardaria decenas de horas.
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
            elif MODO_RECALCULO and abs(float(act) - valor) > TOLERANCIA:
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

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Carga historica PBF")
    parser.add_argument("--desde", help="YYYY-MM-DD (por defecto START_DATE)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (por defecto END_DATE)")
    parser.add_argument("--recalculo", action="store_true",
                        help="Sobrescribe valores existentes que difieran")
    args = parser.parse_args()

    global MODO_RECALCULO
    if args.recalculo:
        MODO_RECALCULO = True

    start = date.fromisoformat(args.desde or START_DATE)
    end   = date.fromisoformat(args.hasta or END_DATE)

    _, db_config = load_config()
    creds   = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    headers = get_headers(creds)
    conn    = psycopg2.connect(**db_config)

    chunks = bloques(start, end, CHUNK_DAYS)
    n_ind  = sum(len(v) for v in TABLAS.values())

    log.info("=" * 66)
    log.info(f"  Carga historica PBF — {', '.join(TABLAS)}")
    log.info(f"  Periodo      : {start} a {end}")
    log.info(f"  Bloques      : {len(chunks)} de {CHUNK_DAYS} dias")
    log.info(f"  Indicadores  : {n_ind} ({len(IND_GEN)} gen + {len(IND_LOAD)} load "
             f"+ {len(IND_BIL)} bil)")
    log.info(f"  Agregacion   : time_trunc=hour & time_agg={TIME_AGG}  (MWh)")
    log.info(f"  Recalculo    : {MODO_RECALCULO}")
    log.info(f"  Peticiones   : ~{len(chunks) * n_ind:,}")
    log.info("=" * 66)

    contador = {}
    t_ini = time.time()

    for i, (c_ini, c_fin) in enumerate(chunks, 1):
        t0 = time.time()
        ins_chunk = upd_chunk = 0

        for tabla, inds in TABLAS.items():
            target = target_hours(c_ini, c_fin)
            exp    = expected_hours(c_ini, c_fin)

            for ind_id, col in inds.items():
                existentes, con_nulls = estado_chunk(conn, tabla, col, c_ini, c_fin)
                faltan = target - existentes

                if not faltan and not con_nulls and not MODO_RECALCULO:
                    continue

                datos = fetch_chunk(ind_id, c_ini, c_fin, headers)
                time.sleep(PAUSE_SEC)
                if not datos:
                    continue

                nuevos     = [(ts, v) for ts, v in datos.items() if ts in faltan]
                existentes_r = [(ts, v) for ts, v in datos.items()
                                if ts in existentes and ts in exp]

                n_i = insert_new(conn, tabla, col, nuevos) if nuevos else 0
                n_u = upsert_col(conn, tabla, col, existentes_r) if existentes_r else 0

                if n_i:
                    contador[f"{tabla}.{col}"] = contador.get(f"{tabla}.{col}", 0) + n_i
                if n_u:
                    k = f"{tabla}.{col}"
                    contador[k] = contador.get(k, 0) + n_u
                ins_chunk += n_i
                upd_chunk += n_u

        pct = i / len(chunks) * 100
        eta = (time.time() - t_ini) / i * (len(chunks) - i) / 60
        log.info(f"[{i}/{len(chunks)}] {c_ini} a {c_fin}: "
                 f"{ins_chunk} ins / {upd_chunk} upd "
                 f"({time.time()-t0:.0f}s | {pct:.0f}% | ETA {eta:.0f} min)")

    conn.close()

    log.info("\n" + "=" * 66)
    log.info("  RESUMEN DE CELDAS ESCRITAS POR COLUMNA")
    log.info("=" * 66)
    if not contador:
        log.info("  Ninguna celda necesito escritura.")
    else:
        for k, v in sorted(contador.items(), key=lambda x: -x[1]):
            log.info(f"  {k:44s}: {v:>8,} celdas")
        log.info("-" * 66)
        log.info(f"  {'TOTAL':44s}: {sum(contador.values()):>8,} celdas")
    log.info(f"  Duracion: {(time.time()-t_ini)/60:.0f} min")
    log.info("=" * 66)


if __name__ == "__main__":
    main()
