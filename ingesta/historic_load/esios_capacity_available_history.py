"""
TFM Energia UCM - Carga historica de potencia DISPONIBLE (2020-actualidad)
Descarga y carga ANO A ANO, por bloques.

CAMBIOS 15/08/2026
------------------
1. BUG DE FECHA CORREGIDO (critico). La linea era:

       peninsula["fecha"] = pd.to_datetime(peninsula["datetime"], utc=True).dt.date

   ESIOS devuelve "2024-06-12T00:00:00+02:00". Al pasar a UTC eso es
   "2024-06-11T22:00:00Z", y .dt.date extrae el DIA ANTERIOR. Verificado:
   el valor 11952.475 que la API publica para el 12-jun-2024 estaba
   almacenado bajo la fecha 11-jun-2024. TODA la serie cargada por este
   script estaba desplazada un dia hacia atras.

   El patron correcto cuando se necesita la fecha local es NO forzar UTC,
   o bien utc=True seguido de tz_convert("Europe/Madrid") -que es lo que
   hacen spot_price_history y esios_forecast_da_recalculo, ambos correctos-.

   Consecuencia colateral ya resuelta: la fila espuria de 2019-12-31, que
   era el 1-ene-2020 desplazado.

2. ELIMINADO el indicador 479 / gas_turbine_mw: solo publica para Asturias
   y desde jul-2025, con valor constante. Columna eliminada de la tabla.

3. ELIMINADO updated_at del INSERT: la columna ya no existe.

4. COALESCE en el DO UPDATE: un fallo puntual de la API no machaca datos
   buenos con NULL.

5. total_mw no se escribe: es GENERATED (suma de las 6 tecnologias).

6. Flag --forzar para redescargar bloques ya presentes. Necesario para
   reparar el desplazamiento de fechas del punto 1.

Uso
---
    python esios_capacity_available_history.py
    python esios_capacity_available_history.py --forzar
    python esios_capacity_available_history.py --desde 2020 --hasta 2022 --forzar
"""

import argparse
import calendar
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ══════════════════════════════════════════════════════════════════
START_YEAR, START_MONTH = 2020, 1
END_YEAR, END_MONTH = 2026, 8
# ══════════════════════════════════════════════════════════════════

PENINSULA_GEO_ID = 8741

INDICATORS_AVAILABLE = {
    472: "hydro_mw",
    473: "pump_mw",
    474: "nuclear_mw",
    475: "coal_antracita_mw",
    477: "ccgt_mw",
    478: "fuel_mw",
}


def rango_anual(start: date, end: date):
    tramos = []
    y = start.year
    while y <= end.year:
        ini = date(y, 1, 1) if y > start.year else start
        fin = date(y, 12, 31) if y < end.year else end
        tramos.append((ini, fin))
        y += 1
    return tramos


def dias_ya_cargados(db_config, ini: date, fin: date) -> int:
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(DISTINCT (datetime AT TIME ZONE 'Europe/Madrid')::date) "
        "FROM esios_capacity_available "
        "WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date "
        "BETWEEN %s AND %s", (ini, fin))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def fetch_indicator_chunk(headers, indicator_id, start: date, end: date,
                          reintentos=2):
    start_str = start.strftime("%Y-%m-%dT00:00:00")
    end_str = end.strftime("%Y-%m-%dT23:59:00")

    for intento in range(reintentos + 1):
        try:
            resp = requests.get(
                f"https://api.esios.ree.es/indicators/{indicator_id}",
                headers=headers,
                params={
                    "start_date": start_str,
                    "end_date": end_str,
                    "time_trunc": "hour",
                    "time_agg": "avg",
                    "geo_agg": "sum",
                    "geo_trunc": "electric_system",
                },
                timeout=90
            )
            if resp.status_code != 200:
                print(f"    ERROR HTTP {resp.status_code}, intento {intento+1}")
                if intento < reintentos:
                    time.sleep(3)
                    continue
                return None

            values = resp.json().get("indicator", {}).get("values", [])
            if not values:
                return None

            df = pd.json_normalize(values)
            peninsula = df[df["geo_id"] == PENINSULA_GEO_ID].copy()
            if peninsula.empty:
                return None

            # utc=True + tz_convert("Europe/Madrid"): el mismo patron que
            # usan spot_price_history y esios_forecast_da_recalculo.
            #
            # utc=True es OBLIGATORIO aqui: al pedir un anio completo la
            # respuesta mezcla offsets (+01:00 invierno, +02:00 verano) y sin
            # el pandas devuelve dtype object, con lo que .dt falla.
            # Pero NO basta con utc=True: hay que reconvertir a Madrid antes
            # de extraer .date, o la medianoche local cae en el dia anterior
            # en UTC y toda la serie se desplaza un dia (bug corregido, ver
            # nota 1 de la cabecera).
            peninsula["ts"] = (
                pd.to_datetime(peninsula["datetime"], utc=True)
                  .dt.tz_convert("Europe/Madrid"))

            return peninsula[["ts", "value"]]

        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}, intento {intento+1}")
            if intento < reintentos:
                time.sleep(3)
                continue
            return None

    return None


def descargar_bloque(headers, ini: date, fin: date) -> pd.DataFrame:
    series = {}
    for ind_id, col in INDICATORS_AVAILABLE.items():
        s = fetch_indicator_chunk(headers, ind_id, ini, fin)
        if s is not None:
            series[col] = s.set_index("ts")["value"]
        time.sleep(0.4)

    if not series:
        return pd.DataFrame()

    df = pd.DataFrame(series)


    df = df.round(2)
    df.index.name = "datetime"
    return df.sort_index()


def upsert_bloque(db_config, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    cols = list(df.columns)
    col_names = ", ".join(cols)
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, esios_capacity_available.{c})"
        for c in cols)

    rows = [[ts] + [float(row[c]) if pd.notna(row[c]) else None
                       for c in cols]
            for ts, row in df.iterrows()]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    sql = f"""
        INSERT INTO esios_capacity_available (datetime, {col_names})
        VALUES %s
        ON CONFLICT (datetime) DO UPDATE SET {updates}
    """
    execute_values(cur, sql, rows, page_size=200)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", type=int, default=START_YEAR)
    p.add_argument("--hasta", type=int, default=END_YEAR)
    p.add_argument("--forzar", action="store_true",
                   help="Redescargar bloques ya presentes en BD")
    args = p.parse_args()

    start = date(args.desde, START_MONTH, 1)
    if args.hasta >= END_YEAR:
        ult = calendar.monthrange(END_YEAR, END_MONTH)[1]
        end = min(date(END_YEAR, END_MONTH, ult), date.today())
    else:
        end = date(args.hasta, 12, 31)

    print(f"Historico potencia DISPONIBLE: {start} a {end}"
          + ("  [FORZANDO]" if args.forzar else ""))
    print("Fechas en hora LOCAL de Madrid (bug de desplazamiento corregido).\n")

    headers, db_config = load_config()
    total = 0

    for ini, fin in rango_anual(start, end):
        esperados = (fin - ini).days + 1
        print(f"\n{'=' * 60}")
        print(f"BLOQUE {ini.year} - {ini} a {fin}")
        print(f"{'=' * 60}")

        ya = dias_ya_cargados(db_config, ini, fin)
        print(f"  Dias ya en BD: {ya}/{esperados}")

        if ya >= esperados and not args.forzar:
            print("  Completo, se omite (usa --forzar para rehacerlo).")
            continue

        df = descargar_bloque(headers, ini, fin)
        if df.empty:
            print("  Sin datos descargados, se omite.")
            continue

        print(f"  {len(df)} filas descargadas "
              f"({df.index.min()} .. {df.index.max()})")

        n = upsert_bloque(db_config, df)
        total += n
        print(f"  {n} filas cargadas/actualizadas.")

    print(f"\n{'=' * 60}")
    print(f"FINALIZADO - {total} filas cargadas/actualizadas")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()