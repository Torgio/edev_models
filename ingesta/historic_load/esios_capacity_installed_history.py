"""
TFM Energia UCM — Carga historica de potencia INSTALADA (2020-actualidad)
Descarga y carga MES A MES, automaticamente, sin repetir lo ya cargado.
Usa los 25 indicadores ESIOS (1475-1491, 1945, 2272-2366, 10300-10517).
"""

import sys
import time
import calendar
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
from datetime import date

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import load_config


# ══════════════════════════════════════════════════════════════════
# RANGO A CARGAR
# ══════════════════════════════════════════════════════════════════
START_YEAR = 2020
START_MONTH = 1

END_YEAR = 2026
END_MONTH = 7
# ══════════════════════════════════════════════════════════════════

PENINSULA_GEO_ID = 8741

INDICATORS_INSTALLED = {
    1475:  "hydro_mw",
    1476:  "pump_mw",
    1477:  "nuclear_mw",
    1478:  "coal_mw",
    1479:  "diesel_mw",
    1480:  "gas_turbine_mw",
    1482:  "fuel_mw",
    1483:  "ccgt_mw",
    1484:  "hydro_wind_mw",
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


def rango_mensual(start: date, end: date):
    """Genera el primer dia de cada mes desde start hasta end inclusive."""
    meses = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        meses.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return meses


def mes_ya_cargado(db_config, mes: date) -> bool:
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM esios_capacity_installed WHERE date = %s", (mes,))
    existe = cur.fetchone()[0] > 0
    cur.close()
    conn.close()
    return existe


def fetch_indicator_month(headers, indicator_id, mes: date, reintentos=2):
    """Descarga un indicador para un mes concreto, agregado a Peninsula."""
    start = mes.strftime("%Y-%m-%dT00:00:00")
    end = mes.strftime("%Y-%m-28T23:00:00")

    for intento in range(reintentos + 1):
        try:
            resp = requests.get(
                f"https://api.esios.ree.es/indicators/{indicator_id}",
                headers=headers,
                params={
                    "start_date": start,
                    "end_date": end,
                    "time_trunc": "month",
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

            return float(peninsula["value"].iloc[-1])

        except Exception as e:
            if intento < reintentos:
                time.sleep(2)
                continue
            print(f"    Indicador {indicator_id}: ERROR — {str(e)[:80]}")
            return None

    return None


def build_row(headers, mes: date) -> dict:
    row = {}
    for ind_id, col in INDICATORS_INSTALLED.items():
        row[col] = fetch_indicator_month(headers, ind_id, mes)
        time.sleep(0.2)
    return row


def upsert_mes(db_config, mes: date, row: dict):
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()

    cols = list(row.keys())
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])

    valores = [float(row[c]) if row[c] is not None else None for c in cols]

    sql = f"""
        INSERT INTO esios_capacity_installed (date, {col_names})
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET {updates}
    """
    template = "(" + ", ".join(["%s"] * (len(cols) + 1)) + ")"

    execute_values(cur, sql, [[mes] + valores], template=template)
    conn.commit()
    cur.close()
    conn.close()


def main():
    start = date(START_YEAR, START_MONTH, 1)
    end = date(END_YEAR, END_MONTH, 1)

    print(f"Cargando historico de potencia INSTALADA: {start} a {end}")
    print("(mes a mes, automatico, sin repetir lo ya cargado)\n")

    headers, db_config = load_config()
    meses = rango_mensual(start, end)
    total_cargados = 0
    total_omitidos = 0

    for mes in meses:
        if mes_ya_cargado(db_config, mes):
            print(f"{mes.strftime('%Y-%m')}: ya cargado, se omite.")
            total_omitidos += 1
            continue

        print(f"{mes.strftime('%Y-%m')}: descargando...")
        row = build_row(headers, mes)
        con_dato = sum(1 for v in row.values() if v is not None)
        print(f"  {con_dato}/{len(row)} columnas con dato")

        upsert_mes(db_config, mes, row)
        total_cargados += 1
        print(f"  Cargado en BD.")

    print(f"\n{'='*60}")
    print(f"FINALIZADO — {total_cargados} meses cargados, {total_omitidos} ya existentes omitidos")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()