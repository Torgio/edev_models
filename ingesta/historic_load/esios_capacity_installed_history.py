"""
TFM Energia UCM - Carga historica de potencia INSTALADA (2020-actualidad)

La potencia instalada es un dato MENSUAL. Se descarga una vez por mes y se
escribe en TODOS los dias de ese mes, igual que hace el pipeline diario.

CAMBIOS 15/08/2026
------------------
1. ESCRIBE TODOS LOS DIAS DEL MES, no solo el dia 1. Antes insertaba una unica
   fila con date = primer dia del mes, lo que dejaba la tabla incoherente con
   el pipeline diario (que si cubre todos los dias) y hacia que un mes
   recargado quedara con el dia 1 actualizado y el resto con el valor viejo.

2. time_agg=average anadido. Con time_trunc=month y sin time_agg el default de
   ESIOS es SUM. Hoy no infla porque los indicadores son nativos mensuales,
   pero si ESIOS cambiara la granularidad -como hizo con el 600 en enero de
   2025- el error entraria en silencio y con valores plausibles.

3. end_date pasa del dia 28 fijo al ultimo dia real del mes. El 28 hardcodeado
   recortaba hasta 3 dias de cada mes.

4. COALESCE en el DO UPDATE: un fallo puntual de la API no machaca con NULL
   hasta 31 dias de datos buenos.

5. Flag --forzar. Sin el, un mes ya presente se omite (comportamiento
   historico). Con el, se redescarga y sobrescribe: necesario para corregir
   meses ya cargados, que antes era imposible.

Indicadores: 23. Eliminados 1479 (diesel), 1480 (turbina de gas) y 1484
(hidroeolica): cero dato peninsular, son tecnologias insulares.

Uso
---
    python esios_capacity_installed_history.py
    python esios_capacity_installed_history.py --forzar
    python esios_capacity_installed_history.py --desde 2026-01 --hasta 2026-07 --forzar
"""

import argparse
import calendar
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ══════════════════════════════════════════════════════════════════
# RANGO POR DEFECTO (sobreescribible con --desde / --hasta)
# ══════════════════════════════════════════════════════════════════
START_YEAR, START_MONTH = 2020, 1
END_YEAR, END_MONTH = 2026, 7
# ══════════════════════════════════════════════════════════════════

PENINSULA_GEO_ID = 8741

INDICATORS_INSTALLED = {
    1475:  "hydro_mw",
    1476:  "pump_mw",
    1477:  "nuclear_mw",
    1478:  "coal_mw",
    1482:  "fuel_mw",
    1483:  "ccgt_mw",
    1485:  "wind_mw",
    1486:  "solar_pv_mw",
    1487:  "solar_thermal_mw",
    1488:  "other_renewable_mw",
    1489:  "cogeneration_mw",
    1490:  "waste_nonrenewable_mw",
    1491:  "waste_renewable_mw",
    1945:  "autoconsume_solar_pv_mw",
    2272:  "solar_pv_hybrid_mw",
    2273:  "wind_hybrid_mw",
    2275:  "battery_hybrid_mw",
    2366:  "autoconsume_battery_mw",
    10300: "total_mw",
    10301: "total_nonrenewable_mw",
    10302: "total_renewable_mw",
    10413: "total_autoconsume_mw",
    10517: "total_hybrid_mw",
}


def ultimo_dia(mes: date) -> date:
    return mes.replace(day=calendar.monthrange(mes.year, mes.month)[1])


def rango_mensual(start: date, end: date):
    meses = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        meses.append(date(y, m, 1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return meses


def mes_ya_cargado(db_config, mes: date) -> bool:
    """Completo = tantas filas como dias tiene el mes."""
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM esios_capacity_installed "
        "WHERE date >= %s AND date <= %s", (mes, ultimo_dia(mes)))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n >= calendar.monthrange(mes.year, mes.month)[1]


def fetch_indicator_month(headers, indicator_id, mes: date, reintentos=2):
    start = mes.strftime("%Y-%m-%dT00:00:00")
    end = ultimo_dia(mes).strftime("%Y-%m-%dT23:59:00")

    for intento in range(reintentos + 1):
        try:
            resp = requests.get(
                f"https://api.esios.ree.es/indicators/{indicator_id}",
                headers=headers,
                params={
                    "start_date": start,
                    "end_date": end,
                    "time_trunc": "month",
                    # Obligatorio: sin esto el default de ESIOS es SUM.
                    "time_agg": "average",
                    "geo_agg": "sum",
                    "geo_trunc": "electric_system",
                },
                timeout=30
            )
            if resp.status_code != 200:
                if intento < reintentos:
                    time.sleep(2)
                    continue
                return None

            values = resp.json().get("indicator", {}).get("values", [])
            if not values:
                return None

            df = pd.json_normalize(values)
            peninsula = df[df["geo_id"] == PENINSULA_GEO_ID]
            if peninsula.empty:
                return None

            return round(float(peninsula["value"].iloc[-1]), 2)

        except Exception as e:
            if intento < reintentos:
                time.sleep(2)
                continue
            print(f"    Indicador {indicator_id}: ERROR - {str(e)[:80]}")
            return None

    return None


def build_row(headers, mes: date) -> dict:
    row = {}
    for ind_id, col in INDICATORS_INSTALLED.items():
        row[col] = fetch_indicator_month(headers, ind_id, mes)
        time.sleep(0.2)
    return row


def upsert_mes(db_config, mes: date, row: dict) -> int:
    """Replica el valor mensual en todos los dias del mes."""
    cols = list(row.keys())
    col_names = ", ".join(cols)
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, esios_capacity_installed.{c})"
        for c in cols)
    valores = [float(row[c]) if row[c] is not None else None for c in cols]

    fin = ultimo_dia(mes)
    dias = [mes + timedelta(days=k) for k in range((fin - mes).days + 1)]
    filas = [[d] + valores for d in dias]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    sql = f"""
        INSERT INTO esios_capacity_installed (date, {col_names})
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET {updates}
    """
    execute_values(cur, sql, filas)
    conn.commit()
    cur.close()
    conn.close()
    return len(filas)


def mes_iso(s: str) -> date:
    """Acepta 'YYYY-MM'."""
    y, m = s.split("-")
    return date(int(y), int(m), 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", type=mes_iso,
                   default=date(START_YEAR, START_MONTH, 1))
    p.add_argument("--hasta", type=mes_iso,
                   default=date(END_YEAR, END_MONTH, 1))
    p.add_argument("--forzar", action="store_true",
                   help="Redescargar y sobrescribir meses ya presentes")
    args = p.parse_args()

    print(f"Historico potencia INSTALADA: {args.desde:%Y-%m} a {args.hasta:%Y-%m}"
          + ("  [FORZANDO SOBRESCRITURA]" if args.forzar else ""))
    print("Un valor mensual por indicador, replicado en todos los dias.\n")

    headers, db_config = load_config()
    cargados = omitidos = filas_total = 0

    for mes in rango_mensual(args.desde, args.hasta):
        if not args.forzar and mes_ya_cargado(db_config, mes):
            print(f"{mes:%Y-%m}: completo, se omite.")
            omitidos += 1
            continue

        print(f"{mes:%Y-%m}: descargando...")
        row = build_row(headers, mes)
        con_dato = sum(1 for v in row.values() if v is not None)
        print(f"  {con_dato}/{len(row)} indicadores con dato")

        if con_dato == 0:
            print("  Sin datos, no se escribe nada.")
            continue

        n = upsert_mes(db_config, mes, row)
        cargados += 1
        filas_total += n
        print(f"  {n} dias escritos.")

    print(f"\n{'=' * 60}")
    print(f"FINALIZADO - {cargados} meses cargados ({filas_total} filas), "
          f"{omitidos} omitidos")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()