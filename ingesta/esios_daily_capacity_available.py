"""
TFM Energia UCM — Pipeline DIARIO de potencia DISPONIBLE (generacion convencional)
Revisa los ultimos N dias (variable DIAS_ATRAS abajo), y para cada dia que
NO exista ya en la BD, lo descarga y lo carga. Los dias ya presentes se
omiten sin llamar a la API - evita recargas innecesarias y permite auto-
recuperarse si el cron fallo algun dia.

Cron job (servidor):
    5 21 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_daily_capacity_available.py >> /home/ubuntu/scripts/logs/cron_capacity_available.log 2>&1
"""

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent))
from config import load_config

# ══════════════════════════════════════════════════════════════════
# Cuantos dias atras revisar (incluyendo hoy)
# ══════════════════════════════════════════════════════════════════
DIAS_ATRAS = 30
# ══════════════════════════════════════════════════════════════════

PENINSULA_GEO_ID = 8741

INDICATORS_AVAILABLE = {
    472: "hydro_mw",
    473: "pump_mw",
    474: "nuclear_mw",
    475: "coal_antracita_mw",
    476: "coal_subbituminosa_mw",
    477: "ccgt_mw",
    478: "fuel_mw",
    479: "gas_turbine_mw",
}


def dias_ya_en_bd(db_config, fechas: list) -> set:
    """Devuelve el subconjunto de 'fechas' que YA existen en la tabla."""
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        "SELECT date FROM esios_capacity_available WHERE date = ANY(%s)",
        (fechas,)
    )
    existentes = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return existentes


def fetch_indicator_daily_avg(headers, indicator_id, dia: date):
    start = dia.strftime("%Y-%m-%dT00:00:00")
    end = dia.strftime("%Y-%m-%dT23:00:00")

    try:
        resp = requests.get(
            f"https://api.esios.ree.es/indicators/{indicator_id}",
            headers=headers,
            params={
                "start_date": start,
                "end_date": end,
                "time_trunc": "day",
                "time_agg": "avg",
                "geo_agg": "sum",
                "geo_trunc": "electric_system",
            },
            timeout=30
        )
        if resp.status_code != 200:
            print(f"    Indicador {indicator_id}: ERROR HTTP {resp.status_code}")
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
        print(f"    Indicador {indicator_id}: ERROR — {str(e)[:80]}")
        return None


def build_row(headers, dia: date) -> dict:
    raw = {}
    for ind_id, col in INDICATORS_AVAILABLE.items():
        raw[col] = fetch_indicator_daily_avg(headers, ind_id, dia)
        time.sleep(0.3)

    coal_cols = ["coal_antracita_mw", "coal_subbituminosa_mw"]
    coal_vals = [raw.get(c) for c in coal_cols if raw.get(c) is not None]
    coal_mw = round(sum(coal_vals), 2) if coal_vals else None

    row = {
        "hydro_mw":       raw.get("hydro_mw"),
        "pump_mw":        raw.get("pump_mw"),
        "nuclear_mw":     raw.get("nuclear_mw"),
        "coal_mw":        coal_mw,
        "ccgt_mw":        raw.get("ccgt_mw"),
        "fuel_mw":        raw.get("fuel_mw"),
        "gas_turbine_mw": raw.get("gas_turbine_mw"),
    }

    no_nulos = [v for v in row.values() if v is not None]
    row["total_mw"] = round(sum(no_nulos), 2) if no_nulos else None

    return row


def upsert_dia(db_config, dia: date, row: dict):
    cols = list(row.keys())
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])
    valores = [round(float(row[c]), 2) if row[c] is not None else None for c in cols]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    sql = f"""
        INSERT INTO esios_capacity_available (date, {col_names}, updated_at)
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET
            {updates},
            updated_at = now()
    """
    template = "(" + ", ".join(["%s"] * (len(cols) + 1)) + ", now())"
    execute_values(cur, sql, [[dia] + valores], template=template)
    conn.commit()
    cur.close()
    conn.close()


def main():
    print(f"Pipeline diario potencia DISPONIBLE — {datetime.now()}")
    print(f"Revisando ultimos {DIAS_ATRAS} dias\n")

    headers, db_config = load_config()
    hoy = date.today()

    fechas_rango = [hoy - timedelta(days=i) for i in range(DIAS_ATRAS)]
    fechas_rango.sort()

    existentes = dias_ya_en_bd(db_config, fechas_rango)
    fechas_faltantes = [f for f in fechas_rango if f not in existentes]

    print(f"Dias en rango: {len(fechas_rango)} | Ya en BD: {len(existentes)} | Faltan: {len(fechas_faltantes)}")

    if not fechas_faltantes:
        print("Todos los dias del rango ya estan cargados. Nada que hacer.")
        return

    for dia in fechas_faltantes:
        print(f"\n{dia}: descargando...")
        row = build_row(headers, dia)
        con_dato = sum(1 for v in row.values() if v is not None)
        print(f"  {con_dato}/{len(row)} columnas con dato")

        upsert_dia(db_config, dia, row)
        print(f"  Guardado en BD para {dia}")

    print(f"\nFinalizado — {len(fechas_faltantes)} dias cargados/actualizados")


if __name__ == "__main__":
    main()