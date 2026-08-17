"""
TFM Energia UCM - Carga HISTORICA de Trayport desde /api/trades

El hallazgo
-----------
El pipeline anterior usaba /api/snapshots, que devuelve UN precio por contrato
e IGNORA snapshotDate. Verificado el 14/08/2026: pedir el 11, 12 y 13 de agosto
devolvia las tres veces 60.40 con el mismo dealDate. De ahi la conclusion --
equivocada -- de que no habia historico recuperable.

/api/trades SI acepta rango temporal y devuelve las OPERACIONES INDIVIDUALES:
    from, until, contractType, instrumentId, sequenceId, sequenceItemId
    -> [{tradeId, venueCode, dealDate, price, quantity, aggressorBuy}, ...]

Verificado hasta junio de 2020. Cobertura del TTF Oct de cada anio:
    2020   7.80 EUR/MWh    (minimo de la pandemia)
    2021  25-28
    2022  91-92            (crisis energetica)
    2023  33-45
    2024  34-40
    2026  53-60
Los tres regimenes de precio que el modelo necesita ver.

Mapeo de contratos (verificado 9/9 contra respuestas reales)
------------------------------------------------------------
El tradeId lleva el contrato dentro, y eso permitio confirmarlo:
    "Eurex T7/G3BM102024-20240603/4599/1 Public"  -> G3BM 10 2024 = Oct-24
    "Eurex T7/FEUA122025-20240603/362/1 Public"   -> FEUA 12 2025 = Dic-25

    TTF: item = 272 + meses desde Ago-2026        (1 unidad por mes)
         202=Oct-20  214=Oct-21  226=Oct-22  238=Oct-23  250=Oct-24  272=Ago-26
    EUA: item = 830 + 40*(anio - 2026)            (40 unidades por anio)
         790=Dic-25  830=Dic-26  870=Dic-27

Que guarda
----------
trayport_trades       una fila por operacion. El dato crudo, irrepetible.
trayport_daily_ohlc   agregado diario por contrato: apertura, maximo, minimo,
                      cierre, media ponderada por volumen (VWAP), volumen,
                      numero de operaciones y % de agresor comprador.

El OHLC se calcula aqui en vez de pedirlo agregado a la API porque asi queda
documentado como se construye, y ademas se obtienen el VWAP y la presion
compradora, que ningun endpoint agregado daria.

NOTA SOBRE FUGA DE INFORMACION
Estos son precios de FUTUROS, no del mercado diario: se negocian de forma
continua durante la sesion y el cierre del dia D-1 es conocido antes de las
12:00 del dia D. Son features validos SIN lag, a diferencia del precio spot
de las zonas vecinas. Aun asi, usar el cierre del dia anterior (lag 1) es lo
prudente y lo que hace cualquier mesa de trading.

Uso
---
  python trayport_history.py                       # usa la CONFIGURACION
  python trayport_history.py --desde 2020-01-01 --hasta 2026-08-14
  python trayport_history.py --commodity TTF
  python trayport_history.py --solo-ohlc           # recalcula el agregado
  python trayport_history.py --validar
"""

import sys
import json
import time
import signal
import argparse
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ==========================================================================
#  CONFIGURACION - EDITA AQUI Y EJECUTA
# ==========================================================================
FECHA_DESDE = "2020-01-01"
FECHA_HASTA = "2026-08-14"

COMMODITY   = "ambas"    # "TTF" | "EUA" | "ambas"

MESES_CURVA = 6          # vencimientos por delante a seguir.
                         # 1 = solo el front month.
                         # 6 = curva corta, permite medir contango.

MESES_ATRAS = 12         # cuanto antes del vencimiento se empieza a pedir
                         # un contrato. La liquidez de un mensual se concentra
                         # en los ultimos meses; con 24 se hacian 2557
                         # peticiones (34 min) y la mayoria volvian vacias.
                         # Con 12 son ~1400 y no se pierde nada util.

SOLO_HUECOS = True       # True = no repite contratos ya cargados
CREAR_ESQUEMA = True
# ==========================================================================

URL = "https://analytics.trayport.com/api/trades"
TIMEOUT = 90
PAUSA = 0.4
REINTENTOS = 2
LOTE = 5000

# La API rechaza rangos mayores: {"until":["Maximum query range is 32 days."]}
# Verificado el 14/08/2026. Cada contrato se trocea en ventanas de este tamano.
MAX_DIAS_RANGO = 30

TABLA_TRADES = "trayport_trades"
TABLA_OHLC = "trayport_daily_ohlc"

TTF = {
    "commodity": "TTF", "unidad": "EUR/MWh",
    "instrumentId": 10002806, "sequenceId": 10000305,
    "ancla_item": 272, "ancla_ym": (2026, 8),
}
EUA = {
    "commodity": "EUA", "unidad": "EUR/t",
    "instrumentId": 10003008, "sequenceId": 10000400,
    "ancla_item": 830, "ancla_anio": 2026, "paso": 40,
}

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

Q3 = Decimal("0.001")
PRECIO_MIN, PRECIO_MAX = Decimal("-100"), Decimal("2000")

_parar = False


def _sigint(s, f):
    global _parar
    if _parar:
        sys.exit(130)
    _parar = True
    print("\nCtrl+C: cerrando el lote y guardando progreso...")


signal.signal(signal.SIGINT, _sigint)


class ErrorEstructural(Exception):
    """No se arregla reintentando."""


def fmt(s):
    s = int(s)
    return f"{s//3600}h {(s%3600)//60:02d}m" if s >= 3600 else f"{s//60}m {s%60:02d}s"


# --------------------------------------------------------------------------
# Contratos
# --------------------------------------------------------------------------
def item_ttf(y: int, m: int) -> int:
    ay, am = TTF["ancla_ym"]
    return TTF["ancla_item"] + (y - ay) * 12 + (m - am)


def item_eua(y: int) -> int:
    return EUA["ancla_item"] + (y - EUA["ancla_anio"]) * EUA["paso"]


def contratos_ttf(desde: date, hasta: date, n_curva: int):
    """
    (itemId, periodo, ini, fin) de cada vencimiento mensual que estuvo vivo.

    Un contrato se negocia hasta el mes anterior a la entrega, con liquidez
    creciente segun se acerca. Se pide desde 2 anios antes del vencimiento
    (los lejanos apenas se negocian, pero pedirlos es barato) hasta el fin
    del mes de entrega.
    """
    out = []
    tot_ini = desde.year * 12 + desde.month
    tot_fin = hasta.year * 12 + hasta.month + n_curva
    for tot in range(tot_ini, tot_fin + 1):
        yy, mm = divmod(tot - 1, 12)
        mm += 1
        tot_ini_c = tot - MESES_ATRAS
        yi, mi = divmod(tot_ini_c - 1, 12)
        ini = max(date(yi, mi + 1, 1), desde)
        f_ult = date(yy + 1, 1, 1) if mm == 12 else date(yy, mm + 1, 1)
        fin = min(f_ult, hasta + timedelta(days=1))
        if fin > ini:
            out.append((item_ttf(yy, mm), f"{MESES[mm-1]}-{str(yy)[2:]}", ini, fin))
    return out


def contratos_eua(desde: date, hasta: date):
    """Los diciembres de referencia: ahi se concentra la liquidez del EUA."""
    out = []
    for y in range(desde.year, hasta.year + 3):
        # el EUA de diciembre se negocia con mucha antelacion: 3 anios
        ini = max(date(y - 3, 1, 1), desde)
        fin = min(date(y + 1, 1, 1), hasta + timedelta(days=1))
        if fin > ini:
            out.append((item_eua(y), f"Dic-{str(y)[2:]}", ini, fin))
    return out


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------
def credenciales():
    aqui = Path(__file__).resolve().parent
    for c in (aqui / "credentials.json", aqui.parent / "credentials.json",
              aqui.parent.parent / "credentials.json"):
        if c.is_file():
            key = json.load(open(c, encoding="utf-8")).get("trayport_api_key")
            if not key:
                raise ErrorEstructural(f'falta "trayport_api_key" en {c}')
            return {"x-api-key": key, "Accept": "application/json"}, c
    raise ErrorEstructural("no se encuentra credentials.json")


def trocear(ini: date, fin: date, dias: int = MAX_DIAS_RANGO):
    """Parte el rango en ventanas de como mucho `dias` (limite de la API)."""
    out, a = [], ini
    while a < fin:
        b = min(a + timedelta(days=dias), fin)
        out.append((a, b))
        a = b
    return out


def bajar_trades(cfg, item, ini: date, fin: date, headers):
    """
    Operaciones de un contrato. Trocea el rango porque la API limita a 32 dias.
    Devuelve (lista, None) o (None, motivo).
    """
    todas, vistos = [], set()
    for a, b in trocear(ini, fin):
        parte, err = _bajar_tramo(cfg, item, a, b, headers)
        if parte is None:
            return None, f"{a}..{b}: {err}"
        for t in parte:
            tid = t.get("tradeId") if isinstance(t, dict) else None
            if tid and tid in vistos:
                continue
            if tid:
                vistos.add(tid)
            todas.append(t)
        time.sleep(PAUSA)
    return todas, None


def _bajar_tramo(cfg, item, ini: date, fin: date, headers):
    """Un tramo de como mucho 32 dias. Devuelve (lista, None) o (None, motivo)."""
    params = {
        "from": f"{ini}T00:00:00Z",
        "until": f"{fin}T00:00:00Z",
        "contractType": "SinglePeriod",
        "instrumentId": cfg["instrumentId"],
        "sequenceId": cfg["sequenceId"],
        "sequenceItemId": item,
    }
    ultimo = "sin respuesta"
    for intento in range(REINTENTOS + 1):
        try:
            r = requests.get(URL, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            ultimo = type(e).__name__
            if intento < REINTENTOS:
                time.sleep(2 * (intento + 1))
                continue
            return None, ultimo
        if r.status_code in (401, 403):
            raise ErrorEstructural(f"HTTP {r.status_code}: token invalido o sin permiso")
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                return None, "respuesta no JSON"
            return (d if isinstance(d, list) else []), None
        # 400 y similares no se arreglan reintentando: se informa del motivo
        try:
            det = r.json().get("errors") or r.json().get("title")
            ultimo = f"HTTP {r.status_code} {str(det)[:80]}"
        except Exception:
            ultimo = f"HTTP {r.status_code}"
        if r.status_code == 400:
            return None, ultimo
        if intento < REINTENTOS:
            time.sleep(2 * (intento + 1))
    return None, ultimo


def a_datetime(dd):
    """dealDate viene en nanosegundos desde epoch."""
    if not isinstance(dd, (int, float)):
        return None
    seg = dd / 1e9 if dd > 1e15 else (dd / 1e3 if dd > 1e12 else dd)
    try:
        return datetime.fromtimestamp(seg, timezone.utc)
    except Exception:
        return None


def limpiar(trades, commodity, periodo, item):
    """Normaliza y descarta lo que no cuadre. Devuelve (filas, n_descartadas)."""
    filas, malas = [], 0
    for t in trades:
        if not isinstance(t, dict):
            malas += 1
            continue
        dt = a_datetime(t.get("dealDate"))
        p = t.get("price")
        tid = t.get("tradeId")
        if dt is None or p is None or not tid:
            malas += 1
            continue
        try:
            precio = Decimal(str(p)).quantize(Q3, rounding=ROUND_HALF_UP)
        except Exception:
            malas += 1
            continue
        if not (PRECIO_MIN <= precio <= PRECIO_MAX):
            malas += 1
            continue
        filas.append((tid, commodity, periodo, item, dt, precio,
                      int(t.get("quantity") or 0),
                      bool(t.get("aggressorBuy")),
                      t.get("venueCode")))
    return filas, malas


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def crear_esquema(conn):
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLA_TRADES} (
            trade_id       text PRIMARY KEY,
            commodity      text NOT NULL,
            periodo        text NOT NULL,
            item_id        integer NOT NULL,
            deal_date      timestamptz NOT NULL,
            precio         numeric NOT NULL,
            cantidad       integer,
            agresor_compra boolean,
            venue          text
        )""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLA_TRADES}_c "
                f"ON {TABLA_TRADES} (commodity, periodo, deal_date)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLA_TRADES}_d "
                f"ON {TABLA_TRADES} (deal_date)")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLA_OHLC} (
            fecha              date NOT NULL,
            commodity          text NOT NULL,
            periodo            text NOT NULL,
            item_id            integer,
            apertura           numeric,
            maximo             numeric,
            minimo             numeric,
            cierre             numeric,
            vwap               numeric,
            volumen            integer,
            n_trades           integer,
            pct_agresor_compra numeric,
            PRIMARY KEY (fecha, commodity, periodo)
        )""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLA_OHLC}_f ON {TABLA_OHLC} (fecha)")
    conn.commit()
    cur.close()
    print(f"Esquema: {TABLA_TRADES} y {TABLA_OHLC} listos")


def contratos_cargados(conn) -> set:
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT commodity, periodo FROM {TABLA_TRADES}")
    r = {(a, b) for a, b in cur.fetchall()}
    cur.close()
    return r


def guardar_trades(conn, filas) -> int:
    """
    Inserta ignorando duplicados: trade_id es unico e inmutable, asi que
    reprocesar un contrato nunca duplica ni pisa nada.
    """
    if not filas:
        return 0
    vistos = {f[0]: f for f in filas}
    rows = list(vistos.values())
    cur = conn.cursor()
    n = 0
    for i in range(0, len(rows), LOTE):
        trozo = rows[i:i + LOTE]
        execute_values(cur,
            f"INSERT INTO {TABLA_TRADES} (trade_id, commodity, periodo, item_id, "
            f"deal_date, precio, cantidad, agresor_compra, venue) VALUES %s "
            f"ON CONFLICT (trade_id) DO NOTHING", trozo)
        n += len(trozo)
    conn.commit()
    cur.close()
    return n


def recalcular_ohlc(conn, commodity=None) -> int:
    """
    Reconstruye el agregado diario desde las operaciones.

    La fecha es la LOCAL de Madrid: los mercados europeos de gas cierran hacia
    las 17-18h y agrupar por UTC partiria mal las sesiones de invierno.
    GREATEST(cantidad,1) evita que una operacion con cantidad 0 o nula
    desaparezca del VWAP.
    """
    filtro = "WHERE commodity = %s" if commodity else ""
    args = (commodity,) if commodity else ()
    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO {TABLA_OHLC}
            (fecha, commodity, periodo, item_id, apertura, maximo, minimo,
             cierre, vwap, volumen, n_trades, pct_agresor_compra)
        SELECT
            (deal_date AT TIME ZONE 'Europe/Madrid')::date,
            commodity, periodo, MIN(item_id),
            (array_agg(precio ORDER BY deal_date ASC))[1],
            MAX(precio), MIN(precio),
            (array_agg(precio ORDER BY deal_date DESC))[1],
            ROUND(SUM(precio * GREATEST(cantidad,1))
                  / NULLIF(SUM(GREATEST(cantidad,1)),0), 3),
            SUM(GREATEST(cantidad,1)),
            COUNT(*),
            ROUND(100.0 * COUNT(*) FILTER (WHERE agresor_compra)
                  / NULLIF(COUNT(*),0), 1)
        FROM {TABLA_TRADES}
        {filtro}
        GROUP BY 1, 2, 3
        ON CONFLICT (fecha, commodity, periodo) DO UPDATE SET
            item_id=EXCLUDED.item_id, apertura=EXCLUDED.apertura,
            maximo=EXCLUDED.maximo, minimo=EXCLUDED.minimo,
            cierre=EXCLUDED.cierre, vwap=EXCLUDED.vwap,
            volumen=EXCLUDED.volumen, n_trades=EXCLUDED.n_trades,
            pct_agresor_compra=EXCLUDED.pct_agresor_compra
    """, args)
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# --------------------------------------------------------------------------
def cargar(conn, cfg, contratos, headers, solo_huecos, ya):
    com = cfg["commodity"]
    print(f"\n{'='*72}\n  {com} ({cfg['unidad']})  --  {len(contratos)} contratos\n{'='*72}")

    ok = vacio = fallo = salt = 0
    total = malas_tot = 0
    fallos_det = []
    t0 = time.time()

    for i, (item, periodo, ini, fin) in enumerate(contratos, 1):
        if _parar:
            print(f"\n  Interrumpido en {periodo} ({i}/{len(contratos)})")
            break
        if solo_huecos and (com, periodo) in ya:
            salt += 1
            continue

        trades, err = bajar_trades(cfg, item, ini, fin, headers)
        if trades is None:
            fallo += 1
            fallos_det.append(f"{periodo}: {err}")
            estado = f"ERROR {err[:40]}"
        elif not trades:
            vacio += 1
            estado = "sin operaciones"
        else:
            filas, malas = limpiar(trades, com, periodo, item)
            n = guardar_trades(conn, filas)
            total += n
            malas_tot += malas
            ok += 1
            estado = f"{n} ops" + (f" ({malas} desc.)" if malas else "")

        el = time.time() - t0
        hechos = ok + vacio + fallo
        eta = (el / hechos) * (len(contratos) - i) if hechos else 0
        print(f"  [{i:>3}/{len(contratos)}] {periodo:<8} item {item:<4} "
              f"{ini} a {fin}  {estado:<24} ETA {fmt(eta)}")

    print(f"\n  --- {com} ({fmt(time.time()-t0)}) ---")
    print(f"  contratos con datos  : {ok}")
    print(f"  sin operaciones      : {vacio}")
    print(f"  saltados             : {salt}")
    print(f"  errores              : {fallo}")
    print(f"  operaciones          : {total}")
    if malas_tot:
        print(f"  registros descartados: {malas_tot}")
    if fallos_det:
        print(f"\n  Motivos de los errores:")
        for d in fallos_det[:10]:
            print(f"    {d}")
        if len(fallos_det) > 10:
            print(f"    ... y {len(fallos_det)-10} mas")
    return total


def validar(conn):
    print(f"\n{'='*72}\n  VALIDACION\n{'='*72}")
    cur = conn.cursor()
    cur.execute(f"""SELECT commodity, COUNT(*), COUNT(DISTINCT periodo),
                           MIN(deal_date)::date, MAX(deal_date)::date
                    FROM {TABLA_TRADES} GROUP BY 1 ORDER BY 1""")
    filas = cur.fetchall()
    if not filas:
        print("  sin datos todavia")
        cur.close()
        return
    print(f"  {'com':<5} {'operaciones':>12} {'contratos':>10} {'desde':>12} {'hasta':>12}")
    for c, n, p, a, b in filas:
        print(f"  {c:<5} {n:>12} {p:>10} {str(a):>12} {str(b):>12}")

    cur.execute(f"""SELECT commodity, COUNT(*), MIN(fecha), MAX(fecha)
                    FROM {TABLA_OHLC} GROUP BY 1 ORDER BY 1""")
    print(f"\n  {TABLA_OHLC}:")
    for c, n, a, b in cur.fetchall():
        print(f"    {c:<5} {n:>8} dias-contrato   {a} -> {b}")

    print(f"\n  Contrato mas negociado de cada anio (el que serviria de front month):")
    cur.execute(f"""
        WITH x AS (
          SELECT EXTRACT(YEAR FROM fecha)::int AS anio, commodity, periodo,
                 SUM(volumen) v
          FROM {TABLA_OHLC} GROUP BY 1,2,3),
        top AS (
          SELECT DISTINCT ON (anio, commodity) anio, commodity, periodo
          FROM x ORDER BY anio, commodity, v DESC)
        SELECT t.anio, t.commodity, t.periodo,
               ROUND(AVG(o.cierre),2), ROUND(MIN(o.minimo),2),
               ROUND(MAX(o.maximo),2), SUM(o.n_trades), COUNT(*)
        FROM top t JOIN {TABLA_OHLC} o
          ON o.commodity=t.commodity AND o.periodo=t.periodo
         AND EXTRACT(YEAR FROM o.fecha)=t.anio
        GROUP BY 1,2,3 ORDER BY 2,1""")
    print(f"    {'anio':<6} {'com':<5} {'contrato':<9} {'media':>8} {'min':>8} "
          f"{'max':>8} {'ops':>8} {'dias':>6}")
    for a, c, p, med, mn, mx, nt, nd in cur.fetchall():
        print(f"    {a:<6} {c:<5} {p:<9} {str(med):>8} {str(mn):>8} "
              f"{str(mx):>8} {nt:>8} {nd:>6}")
    cur.close()


def main():
    p = argparse.ArgumentParser(description="Carga historica de Trayport")
    p.add_argument("--desde")
    p.add_argument("--hasta")
    p.add_argument("--commodity", choices=["TTF", "EUA", "ambas"])
    p.add_argument("--curva", type=int)
    p.add_argument("--solo-huecos", action="store_true")
    p.add_argument("--validar", action="store_true")
    p.add_argument("--solo-ohlc", action="store_true",
                   help="no descarga: solo recalcula el agregado diario")
    a = p.parse_args()

    if not (a.desde or a.hasta or a.commodity):
        a.desde, a.hasta = FECHA_DESDE, FECHA_HASTA
        a.commodity, a.solo_huecos = COMMODITY, SOLO_HUECOS
        origen = "bloque CONFIGURACION"
    else:
        origen = "linea de comandos"
    a.curva = a.curva or MESES_CURVA
    a.commodity = a.commodity or COMMODITY

    print("Carga historica Trayport (/api/trades)")
    print(f"Inicio: {datetime.now()}")

    _, db = load_config()
    conn = psycopg2.connect(**db)

    try:
        if CREAR_ESQUEMA:
            crear_esquema(conn)

        if a.validar:
            validar(conn)
            return
        if a.solo_ohlc:
            print(f"\nRecalculando {TABLA_OHLC} desde las operaciones...")
            print(f"  {recalcular_ohlc(conn)} filas")
            validar(conn)
            return

        desde, hasta = date.fromisoformat(a.desde), date.fromisoformat(a.hasta)
        headers, ruta = credenciales()
        print(f"Parametros desde: {origen}")
        print(f"  rango       : {desde} -> {hasta}")
        print(f"  commodity   : {a.commodity}")
        print(f"  curva       : {a.curva} vencimientos por delante")
        print(f"  modo        : {'solo huecos' if a.solo_huecos else 'RECARGA COMPLETA'}")
        print(f"  credenciales: {ruta}")

        ya = contratos_cargados(conn) if a.solo_huecos else set()
        if ya:
            print(f"  ya cargados : {len(ya)} contratos")

        if a.commodity in ("TTF", "ambas"):
            cargar(conn, TTF, contratos_ttf(desde, hasta, a.curva),
                   headers, a.solo_huecos, ya)
        if a.commodity in ("EUA", "ambas") and not _parar:
            cargar(conn, EUA, contratos_eua(desde, hasta),
                   headers, a.solo_huecos, ya)

        print(f"\nRecalculando {TABLA_OHLC} desde las operaciones...")
        print(f"  {recalcular_ohlc(conn)} filas")
        validar(conn)

    except ErrorEstructural as e:
        print(f"\nERROR ESTRUCTURAL: {e}")
        sys.exit(2)
    finally:
        conn.close()

    print(f"\nFin: {datetime.now()}")


if __name__ == "__main__":
    main()