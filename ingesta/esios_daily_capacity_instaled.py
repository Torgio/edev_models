"""
TFM Energia UCM — Pipeline DIARIO de potencia INSTALADA
Cada dia descarga los 25 indicadores para el mes en curso y guarda UNA fila
con la fecha de HOY.

Cron job (servidor):
    0 21 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_capacity_installed_daily.py >> /home/ubuntu/scripts/logs/cron_capacity_installed.log 2>&1
"""

import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent))
from config import load_config

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


def fetch_indicator_month(headers, indicator_id, hoy: date):
    """Descarga el valor mas reciente del mes en curso para un indicador."""
    start = hoy.replace(day=1).strftime("%Y-%m-%dT00:00:00")
    end = hoy.strftime("%Y-%m-%dT23:00:00")

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
            return None

        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            return None

        df = pd.json_normalize(values)
        peninsula = df[df["geo_id"] == PENINSULA_GEO_ID]

        if peninsula.empty:
            return None

        return float(peninsula["value"].iloc[-1])

    except Exception:
        return None


def build_row(headers, hoy: date) -> dict:
    row = {}
    for ind_id, col in INDICATORS_INSTALLED.items():
        row[col] = fetch_indicator_month(headers, ind_id, hoy)
        time.sleep(0.2)
    return row


def upsert_dia(db_config, hoy: date, row: dict):
    cols = list(row.keys())
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])
    valores = [float(row[c]) if row[c] is not None else None for c in cols]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    sql = f"""
        INSERT INTO esios_capacity_installed (date, {col_names})
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET {updates}
    """
    template = "(" + ", ".join(["%s"] * (len(cols) + 1)) + ")"
    execute_values(cur, sql, [[hoy] + valores], template=template)
    conn.commit()
    cur.close()
    conn.close()


def main():
    print(f"Pipeline diario potencia INSTALADA — {datetime.now()}")
    headers, db_config = load_config()
    hoy = date.today()

    row = build_row(headers, hoy)
    con_dato = sum(1 for v in row.values() if v is not None)
    print(f"  {con_dato}/{len(row)} columnas con dato para {hoy}")

    upsert_dia(db_config, hoy, row)
    print(f"  Guardado en esios_capacity_installed para {hoy}")


if __name__ == "__main__":
    main()