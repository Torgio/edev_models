"""
TFM Energia UCM — Carga del precio del CO2 de despacho (ESIOS 1391)
===================================================================
POR QUE ESTE SCRIPT
La columna commodities.co2_ets viene de Yahoo Finance (ticker CO2.L) y, segun
el propio docstring de commodities_history.py, ese ticker SOLO TIENE DATOS
DESDE OCTUBRE DE 2021. De ahi que enero de 2020 apareciera con NULL: no es un
fallo de carga, es que la fuente no cubre ese periodo.

    Hueco real: 2020-01-01 a 2021-10-17, unos 21 meses.

El indicador 1391 de ESIOS ("precio del CO2 de despacho") cubre ese hueco y
tiene tres ventajas para el TFM:
  - Misma API que ya se usa, con las credenciales ya configuradas.
  - Es el precio que el OPERADOR DEL SISTEMA ESPAÑOL usa para calcular el
    coste de despacho, asi que es el mas pertinente para el mercado español.
  - Fuente publica y citable (REE / ESIOS), sin depender de proveedores
    comerciales ni de entitlements.

DECISION TOMADA — ESIOS pasa a ser la UNICA fuente de CO2
Yahoo Finance ya no actualiza el ticker CO2.L, asi que mantener esa serie
significaba conservar una columna muerta: congelada en su ultima descarga y
haciendo fallar el pipeline cada noche. Se sustituye por completo.

    commodities.co2_ets   <-- ESIOS 1391 (PCO2D), valor mensual por dia

Consecuencias que conviene tener presentes y documentar en la memoria:
  - SE PIERDE LA RESOLUCION DIARIA del periodo oct-2021 a hoy, que Yahoo si
    tenia. El CO2 pasa a ser constante dentro de cada mes, asi que esa
    variable no aportara nada a la prediccion DENTRO del mes; solo captura la
    tendencia entre meses.
  - A cambio, la serie es homogenea, cubre 2020-2026 sin empalmes, viene de
    una fuente oficial y publica (REE / ESIOS) y sigue viva.
  - El indicador se llama PCO2D "de los TNP" (Territorios No Peninsulares)
    porque es el que REE usa para el despacho en Canarias y Baleares, pero el
    derecho de emision es el mismo mercado europeo: el precio es el del EUA.
    Verificado contra referencias externas: 24,84 EUR/t en enero de 2020
    frente a los ~25 de mercado.

MODO_SOBRESCRIBIR controla si se pisan los valores antiguos de Yahoo. Por
defecto True, que es lo que exige la sustitucion: con False solo rellenaria
los NULL y quedaria una serie mezclada.

USO
    python commodities_co2_esios.py --check      # solo inspecciona el indicador
    python commodities_co2_esios.py              # carga 2020-2026
    python commodities_co2_esios.py --desde 2020-01-01 --hasta 2021-12-31
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

sys.path.append(str(Path(__file__).parent))
from config import load_config

BASE_URL   = "https://api.esios.ree.es/indicators"
INDICADOR  = 1391
COLUMNA    = "co2_ets"
TIMEOUT    = 60

# True = sustituye los valores antiguos de Yahoo (lo que exige la decision)
# False = solo rellena NULLs, dejando una serie mezclada de dos fuentes
MODO_SOBRESCRIBIR = True

START_DEFAULT = "2020-01-01"
END_DEFAULT   = date.today().strftime("%Y-%m-%d")

CREDS_PATH = Path(__file__).parent / "credentials.json"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("co2_esios")


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar(headers, desde: str, hasta: str) -> tuple[dict, str]:
    """
    Devuelve ({date: valor}, nombre_indicador).
    No se fuerza time_trunc: se toma la granularidad NATIVA para poder ver
    si el indicador es mensual, diario u otra cosa.
    """
    url = (f"{BASE_URL}/{INDICADOR}"
           f"?start_date={desde}T00:00:00"
           f"&end_date={hasta}T23:59:59")
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    ind = r.json().get("indicator", {})
    nombre = ind.get("name", "")

    out = {}
    for v in ind.get("values", []):
        dt_str = v.get("datetime") or v.get("datetime_utc")
        val = v.get("value")
        if not dt_str or val is None:
            continue
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            out[dt.date()] = float(val)
        except Exception:
            pass
    return out, nombre


def inspeccionar(headers, desde: str, hasta: str):
    """Diagnostico: granularidad, rango y ordenes de magnitud."""
    print("=" * 82)
    print(f"INSPECCION DEL INDICADOR {INDICADOR}")
    print("=" * 82)
    datos, nombre = descargar(headers, desde, hasta)
    print(f"  nombre : {nombre}")
    print(f"  valores: {len(datos)}")
    if not datos:
        print("  Sin datos: revisar el indicador o el rango de fechas.")
        return None

    fechas = sorted(datos)
    print(f"  rango  : {fechas[0]} a {fechas[-1]}")

    # Estimar granularidad por el hueco medio entre observaciones
    if len(fechas) > 1:
        dias = [(fechas[i+1] - fechas[i]).days for i in range(len(fechas)-1)]
        medio = sum(dias) / len(dias)
        if medio > 25:
            gran = "MENSUAL"
        elif medio > 5:
            gran = "SEMANAL"
        elif medio <= 1.6:
            gran = "DIARIA"
        else:
            gran = f"irregular (media {medio:.1f} dias)"
        print(f"  granularidad estimada: {gran} (hueco medio {medio:.1f} dias)")

    vals = list(datos.values())
    print(f"  valores: min {min(vals):.2f}  max {max(vals):.2f}  "
          f"media {sum(vals)/len(vals):.2f} EUR/t")
    print("\n  primeras 10 observaciones:")
    for f in fechas[:10]:
        print(f"    {f}  {datos[f]:>8.2f}")
    if len(fechas) > 10:
        print(f"    ... y {len(fechas)-10} mas")

    print("""
  ORDENES DE MAGNITUD ESPERADOS (para validar que el indicador es el correcto):
    2020        ~25 EUR/t
    2021        ~50 EUR/t (subida fuerte a final de año)
    2022        ~80 EUR/t
    2023        ~85 EUR/t, con pico cerca de 100
    2024        ~65 EUR/t
    2025-2026   ~70-85 EUR/t
  Si los valores no se parecen a esto, el 1391 no es el precio del EUA y hay
  que buscar otro indicador.""")
    print("=" * 82)
    return datos


# ── BD ────────────────────────────────────────────────────────────────────────

def existe_columna(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'commodities' AND column_name = %s
        """, (COLUMNA,))
        return cur.fetchone() is not None


def expandir_a_diario(datos: dict, hasta: date) -> dict:
    """
    Un valor mensual se asigna a TODOS los dias de su mes, para que la columna
    sea utilizable junto a las diarias sin necesidad de un JOIN.
    Se propaga desde cada observacion hasta el dia anterior a la siguiente.
    """
    fechas = sorted(datos)
    diario = {}
    for i, f in enumerate(fechas):
        fin = (fechas[i+1] - timedelta(days=1)) if i + 1 < len(fechas) else hasta
        d = f
        while d <= fin:
            diario[d] = datos[f]
            d += timedelta(days=1)
    return diario


def cargar(conn, diario: dict) -> tuple[int, int]:
    """Inserta filas nuevas y rellena la columna donde este NULL."""
    if not diario:
        return 0, 0

    fechas = sorted(diario)
    with conn.cursor() as cur:
        cur.execute("SELECT fecha FROM commodities WHERE fecha >= %s AND fecha <= %s",
                    (fechas[0], fechas[-1]))
        existentes = {r[0] for r in cur.fetchall()}

    nuevas  = [(f, diario[f]) for f in fechas if f not in existentes]
    upserts = [(f, diario[f]) for f in fechas if f in existentes]

    ins = upd = 0
    if nuevas:
        with conn.cursor() as cur:
            execute_values(cur,
                f"INSERT INTO commodities (fecha, {COLUMNA}) VALUES %s "
                f"ON CONFLICT (fecha) DO NOTHING",
                nuevas, page_size=500)
        conn.commit()
        ins = len(nuevas)

    if upserts:
        # En lote: 1 UPDATE en vez de uno por fila.
        # Con MODO_SOBRESCRIBIR se pisan tambien los valores que ya tengan
        # dato, que es lo que exige sustituir la fuente de Yahoo por ESIOS.
        filtro = "" if MODO_SOBRESCRIBIR else f" AND c.{COLUMNA} IS NULL"
        with conn.cursor() as cur:
            execute_values(cur, f"""
                UPDATE commodities AS c
                SET {COLUMNA} = v.valor
                FROM (VALUES %s) AS v(f, valor)
                WHERE c.fecha = v.f{filtro}
            """, upserts, template="(%s, %s::numeric)", page_size=500)
            upd = cur.rowcount
        conn.commit()

    return ins, upd


# Medias anuales del EUA segun fuentes de mercado, para validar la carga
REFERENCIA_ANUAL = {2020: 25, 2021: 53, 2022: 81, 2023: 84, 2024: 65,
                    2025: 75, 2026: 80}


def validar(conn):
    """
    Control de calidad de la serie cargada: media anual frente a las
    referencias de mercado del EUA. Es lo que permite afirmar en la memoria
    que el indicador 1391 recoge el precio del derecho de emision europeo y
    no una referencia administrativa distinta.
    """
    print("\n" + "=" * 82)
    print("VALIDACION DE LA SERIE CARGADA (co2_ets = ESIOS 1391)")
    print("=" * 82)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT extract(year from fecha)::int       AS anio,
                   count({COLUMNA})                    AS n,
                   round(avg({COLUMNA})::numeric, 2)    AS media,
                   round(min({COLUMNA})::numeric, 2)    AS minimo,
                   round(max({COLUMNA})::numeric, 2)    AS maximo
            FROM commodities
            GROUP BY 1
            ORDER BY 1
        """)
        filas = cur.fetchall()

    if not filas:
        print("  Sin datos.")
        return

    print(f"  {'anio':>6} {'dias':>6} {'media':>9} {'min':>9} {'max':>9} "
          f"{'referencia':>11} {'desvio':>8}")
    for anio, n, media, mn, mx in filas:
        ref = REFERENCIA_ANUAL.get(anio)
        if media is not None and ref:
            desv = f"{(float(media)-ref)/ref*100:+7.1f}%"
            ref_s = f"{ref:>11}"
        else:
            desv, ref_s = f"{'-':>8}", f"{'-':>11}"
        m_s = f"{media:>9}" if media is not None else f"{'-':>9}"
        print(f"  {anio:>6} {n:>6} {m_s} {mn if mn is not None else '-':>9} "
              f"{mx if mx is not None else '-':>9} {ref_s} {desv}")

    print("""
  Un desvio de pocos puntos porcentuales confirma que el 1391 recoge el precio
  del EUA. Desvios grandes indicarian que mide otra cosa.

  LIMITACION A DOCUMENTAR: el valor es MENSUAL repetido en cada dia del mes,
  asi que la columna no tiene variabilidad intramensual. Como feature captura
  la tendencia entre meses, no los movimientos diarios del mercado de
  emisiones. Si el modelo necesitara esa resolucion, la fuente publica con
  dato diario es el Allowance Price Explorer de ICAP
  (icapcarbonaction.com/en/ets-prices), que permite descarga.""")
    print("=" * 82)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Precio CO2 de despacho (ESIOS 1391)")
    p.add_argument("--desde", default=START_DEFAULT)
    p.add_argument("--hasta", default=END_DEFAULT)
    p.add_argument("--check", action="store_true",
                   help="Solo inspecciona el indicador, sin escribir en BD")
    args = p.parse_args()

    headers = get_headers()

    datos = inspeccionar(headers, args.desde, args.hasta)
    if args.check or not datos:
        return

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    if not existe_columna(conn):
        log.error(f"La columna '{COLUMNA}' no existe en commodities.")
        conn.close()
        return
    if MODO_SOBRESCRIBIR:
        log.warning("MODO_SOBRESCRIBIR activo: se van a PISAR los valores "
                    "actuales de co2_ets (los de Yahoo Finance).")

    hasta = date.fromisoformat(args.hasta)
    diario = expandir_a_diario(datos, hasta)
    log.info(f"\n{len(datos)} observaciones -> {len(diario)} dias tras expandir")

    ins, upd = cargar(conn, diario)
    log.info(f"{ins} filas insertadas | {upd} celdas actualizadas")

    validar(conn)
    conn.close()


if __name__ == "__main__":
    main()
