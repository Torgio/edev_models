"""
TFM Energia UCM — Trayport Daily Pipeline
=========================================
Captura diaria del precio de TTF y EUA desde Trayport a la tabla
trayport_daily. Es una PRUEBA EN PRODUCCION con fecha de evaluacion: se deja
correr unas dos semanas y luego se decide si la serie sirve o se descarta.

POR QUE ES UNA PRUEBA Y NO UN PIPELINE DEFINITIVO
Doce tests (401-412) establecieron los limites de esta suscripcion:
  - /api/snapshots devuelve UN precio por contrato y NO varia con snapshotDate:
    el TTF Mar-26 daba 31,765 en noviembre, diciembre, enero y febrero.
  - Al pedir el mismo contrato en 10 fechas consecutivas salio SIEMPRE el mismo
    valor: 58,450 en TTF y 82,440 en EUA. No hay historico recuperable.
  - El libro de ordenes viene vacio (bid, ask y spread nulos en todos los
    productos), asi que no hay punto medio que usar como alternativa.
  - Los entitlements cubren SOLO gas TTF y emisiones: petroleo (12 instrumentos
    de Brent probados), carbon y power de Francia y España devuelven vacio.

  LA HIPOTESIS QUE ESTE SCRIPT PONE A PRUEBA: pedir fechas pasadas devuelve
  siempre el ultimo valor conocido, pero capturar el valor CADA DIA en el
  momento podria dar una serie que si varie. Es distinto: no se pregunta al
  pasado, se registra el presente. Si los valores cambian dia a dia, la serie
  aporta la resolucion diaria que el CO2 de ESIOS (mensual) no tiene.

QUE CARGA — 7 series, 2 peticiones al dia (sequenceItemId acepta lista)
  TTF (EUR/MWh), instrumentId 10002806, sequenceId 10000305:
      Ago-26=272  Sep-26=273  Oct-26=274  Nov-26=275  Dic-26=276
      La curva completa de los meses vivos de 2026. Su forma (contango o
      backwardation entre meses) es informacion sobre las expectativas del
      mercado, no solo un precio.
  EUA (EUR/t), instrumentId 10003008, sequenceId 10000400:
      Dic-26=830  Dic-27=870
      El diciembre es la referencia del mercado de emisiones: el plazo de
      cumplimiento es abril del año siguiente y ahi se concentra la liquidez.
      Con dos vencimientos se puede medir el contango.

REGLAS APRENDIDAS QUE NO HAY QUE CAMBIAR
  - instrumentId, NUNCA marketId (con marketId la API da 404).
  - snapshotDate hasta AYER: el intradia devuelve 401.
  - Los itemId son extrapolables: TTF +1 por mes desde 272=Ago-26,
    EUA +40 por año desde 830=Dic-26. Eso permite el rollover automatico.

TABLA
    CREATE TABLE trayport_daily (
      fecha date NOT NULL, commodity text NOT NULL, periodo text NOT NULL,
      item_id integer NOT NULL, precio numeric, fuente text,
      deal_date timestamptz, antiguedad_h integer,
      updated_at timestamptz DEFAULT now(),
      PRIMARY KEY (fecha, commodity, periodo));

CRON (servidor) — los mercados europeos de gas cierran hacia las 17:00-18:00:
    CRON_TZ=Europe/Madrid
    0 19 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/trayport_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_trayport.log 2>&1

USO
    python trayport_daily_pipeline.py                  # captura de hoy
    python trayport_daily_pipeline.py --dias 14        # ademas, 14 dias atras
    python trayport_daily_pipeline.py --evaluar        # solo el informe
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import psycopg2
from psycopg2.extras import execute_values

from config import load_config

SNAPSHOT_URL = "https://analytics.trayport.com/api/snapshots"
TIMEOUT      = 30
PAUSA_SEC    = 0.4
TABLA        = "trayport_daily"

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

CREDS_PATH = Path(__file__).parent / "credentials.json"

# ── Definicion de productos ───────────────────────────────────────────────────
# Los itemId se calculan, no se escriben a mano, para que el rollover sea
# automatico: cuando el mes en curso avance, la lista de contratos avanza sola.

TTF = {
    "commodity":    "TTF",
    "instrumentId": 10002806,
    "sequenceId":   10000305,
    "unidad":       "EUR/MWh",
    "ancla_item":   272,          # Ago-26
    "ancla_ym":     (2026, 8),
    "n_meses":      5,            # curva de 5 vencimientos consecutivos
}

EUA = {
    "commodity":    "EUA",
    "instrumentId": 10003008,
    "sequenceId":   10000400,
    "unidad":       "EUR/t",
    "ancla_item":   830,          # Dic-26
    "ancla_anio":   2026,
    "paso_anual":   40,
    "anios":        [2026, 2027], # los diciembres de referencia
}

MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("trayport_daily")


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    key = creds.get("trayport_api_key")
    if not key:
        sys.exit('ERROR: falta "trayport_api_key" en credentials.json')
    return {"x-api-key": key, "Accept": "application/json"}


# ── Calculo de contratos (rollover automatico) ────────────────────────────────

def meses_entre(a, b) -> int:
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def contratos_ttf(hoy: date) -> list[tuple[int, str]]:
    """(itemId, periodo) de los N meses consecutivos desde el mes en curso."""
    out, y, m = [], hoy.year, hoy.month
    for _ in range(TTF["n_meses"]):
        iid = TTF["ancla_item"] + meses_entre(TTF["ancla_ym"], (y, m))
        out.append((iid, f"{MESES[m-1]}-{str(y)[2:]}"))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def contratos_eua() -> list[tuple[int, str]]:
    """(itemId, periodo) de los diciembres de referencia."""
    return [(EUA["ancla_item"] + (a - EUA["ancla_anio"]) * EUA["paso_anual"],
             f"Dic-{str(a)[2:]}")
            for a in EUA["anios"]]


# ── Trayport ──────────────────────────────────────────────────────────────────

def pedir(headers, cfg, item_ids, fecha_dt):
    """
    Una peticion con la lista completa de contratos.
    Devuelve {itemId: (precio, fuente, deal_date)}.
    """
    params = {
        "contractType":   "SinglePeriod",
        "instrumentId":   cfg["instrumentId"],
        "sequenceId":     cfg["sequenceId"],
        "sequenceItemId": item_ids,
        "snapshotDate":   fecha_dt.strftime("%Y-%m-%dT23:59:59Z"),
    }
    try:
        r = requests.get(SNAPSHOT_URL, params=params, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        log.warning(f"    {cfg['commodity']}: excepcion {str(e)[:60]}")
        return {}
    if r.status_code != 200:
        log.warning(f"    {cfg['commodity']}: HTTP {r.status_code}")
        return {}
    try:
        data = r.json()
    except Exception:
        log.warning(f"    {cfg['commodity']}: respuesta no JSON")
        return {}

    snaps = (data.get("snapshots", []) if isinstance(data, dict)
             else (data if isinstance(data, list) else [data]))
    out = {}
    for sn in snaps:
        if not isinstance(sn, dict):
            continue
        precio, fuente, deal = None, None, None

        lt = sn.get("lastTrade")
        if isinstance(lt, dict):
            if lt.get("price") is not None:
                precio, fuente = float(lt["price"]), "lastTrade"
            dd = lt.get("dealDate")
            if isinstance(dd, (int, float)):
                # dealDate viene en nanosegundos desde epoch
                seg = dd / 1e9 if dd > 1e15 else (dd / 1e3 if dd > 1e12 else dd)
                try:
                    deal = datetime.fromtimestamp(seg, timezone.utc)
                except Exception:
                    pass

        # El libro viene vacio en esta suscripcion, pero se intenta: si algun
        # dia se habilitase, el mid seria mejor referencia que un trade viejo
        if precio is None:
            bo = sn.get("bestOrders")
            if isinstance(bo, dict):
                b, a = bo.get("bidPrice"), bo.get("askPrice")
                if b is not None and a is not None:
                    precio, fuente = (float(b) + float(a)) / 2, "mid"
                elif b is not None or a is not None:
                    precio, fuente = float(b if b is not None else a), "bid/ask"

        c  = sn.get("contract", {}) or {}
        si = c.get("sequenceItemId") or sn.get("sequenceItemId")
        if si is None and len(item_ids) == 1:
            si = item_ids[0]
        if precio is not None and si is not None:
            out[int(si)] = (precio, fuente, deal)
    return out


# ── BD ────────────────────────────────────────────────────────────────────────

def guardar(conn, filas) -> int:
    """
    Upsert por (fecha, commodity, periodo). Se sobrescribe el precio porque una
    reejecucion del mismo dia debe quedarse con la captura mas reciente.
    """
    if not filas:
        return 0
    with conn.cursor() as cur:
        execute_values(cur, f"""
            INSERT INTO {TABLA}
                (fecha, commodity, periodo, item_id, precio, fuente,
                 deal_date, antiguedad_h)
            VALUES %s
            ON CONFLICT (fecha, commodity, periodo) DO UPDATE SET
                precio       = EXCLUDED.precio,
                fuente       = EXCLUDED.fuente,
                deal_date    = EXCLUDED.deal_date,
                antiguedad_h = EXCLUDED.antiguedad_h,
                updated_at   = now()
        """, filas, page_size=200)
        n = cur.rowcount
    conn.commit()
    return n


def capturar_dia(conn, headers, dia: date) -> int:
    """Captura los 7 contratos para un dia. 2 peticiones."""
    fecha_dt = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
    filas = []

    for cfg, contratos in ((TTF, contratos_ttf(dia)), (EUA, contratos_eua())):
        item_ids = [i for i, _ in contratos]
        nombres  = dict(contratos)
        res = pedir(headers, cfg, item_ids, fecha_dt)
        import time; time.sleep(PAUSA_SEC)

        for iid, periodo in contratos:
            if iid not in res:
                log.debug(f"    {cfg['commodity']} {periodo}: sin precio")
                continue
            precio, fuente, deal = res[iid]
            ant_h = None
            if deal:
                ref = datetime(dia.year, dia.month, dia.day, 23, 59,
                               tzinfo=timezone.utc)
                ant_h = max(0, int((ref - deal).total_seconds() // 3600))
            filas.append((dia, cfg["commodity"], periodo, iid,
                          round(precio, 4), fuente, deal, ant_h))

    n = guardar(conn, filas)
    if filas:
        detalle = " | ".join(
            f"{c} {p}={pr:.3f}" + (f" ({a//24}d)" if a and a >= 24 else "")
            for _, c, p, _, pr, _, _, a in filas)
        log.info(f"  [{dia}] {len(filas)} series -> {detalle}")
    else:
        log.warning(f"  [{dia}] sin datos")
    return n


# ── Informe de evaluacion ─────────────────────────────────────────────────────

def evaluar(conn):
    """
    El informe que decide si esta fuente se mantiene o se descarta.
    La columna clave es valores_distintos: si se acerca al numero de dias, la
    serie tiene variacion real y aporta como feature; si se queda en 1 o 2, la
    columna seria practicamente constante y un modelo la ignoraria.
    """
    print("\n" + "=" * 92)
    print("  EVALUACION DE LA FUENTE")
    print("=" * 92)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT commodity, periodo,
                   count(*)                          AS dias,
                   count(DISTINCT precio)            AS valores_distintos,
                   count(DISTINCT deal_date::date)   AS trades_distintos,
                   round(avg(antiguedad_h)/24.0, 1)  AS antig_media_dias,
                   min(precio), max(precio),
                   min(fecha), max(fecha)
            FROM {TABLA}
            GROUP BY 1, 2 ORDER BY 1, 2
        """)
        filas = cur.fetchall()

    if not filas:
        print("  La tabla esta vacia todavia.")
        print("=" * 92)
        return

    print(f"  {'com':<4} {'periodo':<9} {'dias':>5} {'valores':>8} {'trades':>7} "
          f"{'antig(d)':>9} {'min':>9} {'max':>9}  rango")
    for c, p, d, vd, td, am, mn, mx, f1, f2 in filas:
        print(f"  {c:<4} {p:<9} {d:>5} {vd:>8} {td:>7} "
              f"{str(am if am is not None else '-'):>9} "
              f"{float(mn):>9.3f} {float(mx):>9.3f}  {f1} a {f2}")

    # Veredicto por serie
    print("\n  VEREDICTO POR SERIE")
    for c, p, d, vd, td, am, mn, mx, f1, f2 in filas:
        if d < 5:
            v = "aun pocos dias para juzgar"
        elif vd <= 1:
            v = "CONSTANTE: no aporta nada como feature"
        elif vd / d < 0.3:
            v = "poca variacion: utilizable con reservas"
        else:
            v = "UTIL: hay variacion diaria real"
        print(f"    {c} {p:<9} {vd}/{d} valores distintos -> {v}")

    print("""
  Si tras dos semanas las series siguen constantes, esta fuente se descarta y
  el CO2 se queda con ESIOS 1391 (mensual, ya cargado y verificado) y el gas
  con MIBGAS, que tiene los 365 dias de cada año y es mas pertinente que el
  TTF holandes para el coste de un ciclo combinado peninsular.""")
    print("=" * 92)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Captura diaria Trayport (prueba)")
    p.add_argument("--dias", type=int, default=0,
                   help="Ademas de hoy, captura N dias hacia atras. Ojo: la API "
                        "devuelve el ultimo valor conocido, no el de esa fecha, "
                        "asi que sirve para tener filas, no historico real.")
    p.add_argument("--evaluar", action="store_true",
                   help="Solo muestra el informe, sin capturar")
    args = p.parse_args()

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    if args.evaluar:
        evaluar(conn)
        conn.close()
        return

    headers = get_headers()
    hoy = date.today()
    ttf_c, eua_c = contratos_ttf(hoy), contratos_eua()

    log.info("=" * 74)
    log.info(f"Trayport Daily Pipeline (PRUEBA) — {hoy}")
    log.info(f"TTF: {', '.join(f'{p}({i})' for i, p in ttf_c)}")
    log.info(f"EUA: {', '.join(f'{p}({i})' for i, p in eua_c)}")
    log.info(f"Tabla: {TABLA}  |  2 peticiones por dia")
    log.info("=" * 74)

    # El dia de hoy: la API no da intradia, asi que se pide con fecha de ayer
    # 23:59 pero se guarda con la fecha de HOY, que es cuando se captura.
    ayer = hoy - timedelta(days=1)
    total = capturar_dia(conn, headers, ayer)

    if args.dias > 0:
        log.info(f"\nRellenando {args.dias} dias anteriores")
        log.warning("  Aviso: la API devuelve el ultimo valor conocido para "
                    "cualquier fecha, asi que estas filas tendran todas el "
                    "mismo precio. Sirven para completar la tabla, no como "
                    "historico real.")
        for k in range(2, args.dias + 2):
            total += capturar_dia(conn, headers, hoy - timedelta(days=k))

    log.info(f"\n{total} filas escritas")
    evaluar(conn)
    conn.close()


if __name__ == "__main__":
    main()
