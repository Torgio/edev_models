"""
TFM Energia UCM - Pipeline DIARIO de potencia DISPONIBLE (generacion convencional)

Revisa los ultimos N dias (DIAS_ATRAS) y carga los que falten en la BD.

CAMBIOS 15/08/2026 - migracion de esquema
------------------------------------------
La tabla esios_capacity_available paso de 11 a 8 columnas. Este script se
adapta a eso. Sin estos cambios el pipeline falla con error 42703
("column does not exist") en cada ejecucion:

  - ELIMINADO indicador 479 / gas_turbine_mw. El indicador solo publica para
    Asturias (geo_id=71) y arranca el 14-jul-2025, con valor constante 561,80
    MW en los 397 dias con dato. Varianza cero, sin utilidad como predictor.
  - ELIMINADO updated_at del INSERT y del DO UPDATE. La columna, junto con
    created_at, se elimino de la tabla.
  - total_mw sigue siendo GENERATED (se redefinio sin gas_turbine_mw), asi
    que no se le pasa valor: PostgreSQL lo rechazaria.

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

# =====================================================================
# Cuantos dias atras revisar (incluyendo hoy)
# =====================================================================
DIAS_ATRAS = 30

# Si True, redescarga tambien los dias que YA estan en la BD y sobrescribe
# lo que ESIOS haya revisado. Con False (comportamiento historico) los dias
# presentes se omiten sin llamar a la API: la ventana de DIAS_ATRAS solo
# rellena huecos, nunca actualiza. Ver nota al pie del fichero.
REVISAR_EXISTENTES = False
# =====================================================================

PENINSULA_GEO_ID = 8741

INDICATORS_AVAILABLE = {
    472: "hydro_mw",
    473: "pump_mw",
    474: "nuclear_mw",
    475: "coal_antracita_mw",
    476: "coal_subbituminosa_mw",
    477: "ccgt_mw",
    478: "fuel_mw",
}

# gas_turbine_mw (479) esta en NULL para 2019-2024 a proposito: verificado
# contra la API real (13-ago-2026) que el indicador simplemente no existia
# publicado antes del 14-jul-2025 (HTTP 200 con respuesta vacia en fechas
# anteriores, no es un error ni un problema de geo_id). SI hay capacidad
# real (~560 MW en Peninsula en fechas recientes) -- no es "sin carga
# disponible", es que ESIOS empezo a publicar el indicador tarde. No
# investigar de nuevo.


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
    end = dia.strftime("%Y-%m-%dT23:59:00")

    try:
        resp = requests.get(
            f"https://api.esios.ree.es/indicators/{indicator_id}",
            headers=headers,
            params={
                "start_date": start,
                "end_date": end,
                "time_trunc": "day",
                "time_agg": "avg",
                # geo_trunc reagrega los geos por sistema electrico ANTES de
                # devolverlos, asi que las CCAA peninsulares llegan ya sumadas
                # bajo geo_id=8741. Sin esto, indicadores publicados solo a
                # nivel autonomico no apareceran nunca.
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
        print(f"    Indicador {indicator_id}: ERROR - {str(e)[:80]}")
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
        "hydro_mw":   raw.get("hydro_mw"),
        "pump_mw":    raw.get("pump_mw"),
        "nuclear_mw": raw.get("nuclear_mw"),
        "coal_mw":    coal_mw,
        "ccgt_mw":    raw.get("ccgt_mw"),
        "fuel_mw":    raw.get("fuel_mw"),
    }

    # total_mw NO se calcula aqui: es GENERATED ALWAYS AS ... STORED. Se
    # redefinio el 15-ago-2026 como suma de estas 6 columnas (antes 7, con
    # gas_turbine_mw). Una columna GENERATED rechaza valores explicitos.

    return row


def upsert_dia(db_config, dia: date, row: dict):
    cols = list(row.keys())
    col_names = ", ".join(cols)
    # COALESCE: si la API falla puntualmente en un indicador, EXCLUDED viene
    # a NULL y machacaria un dato bueno ya almacenado.
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, esios_capacity_available.{c})"
        for c in cols)
    valores = [round(float(row[c]), 2) if row[c] is not None else None
               for c in cols]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    sql = f"""
        INSERT INTO esios_capacity_available (date, {col_names})
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET
            {updates}
    """
    execute_values(cur, sql, [[dia] + valores])
    conn.commit()
    cur.close()
    conn.close()


def main():
    print(f"Pipeline diario potencia DISPONIBLE - {datetime.now()}")
    print(f"Revisando ultimos {DIAS_ATRAS} dias"
          + ("  [REVISANDO EXISTENTES]" if REVISAR_EXISTENTES else "") + "\n")

    headers, db_config = load_config()
    hoy = date.today()

    fechas_rango = [hoy - timedelta(days=i) for i in range(DIAS_ATRAS)]
    fechas_rango.sort()

    existentes = dias_ya_en_bd(db_config, fechas_rango)
    if REVISAR_EXISTENTES:
        fechas_objetivo = fechas_rango
    else:
        fechas_objetivo = [f for f in fechas_rango if f not in existentes]

    print(f"Dias en rango: {len(fechas_rango)} | Ya en BD: {len(existentes)} "
          f"| A procesar: {len(fechas_objetivo)}")

    if not fechas_objetivo:
        print("Nada que hacer.")
        return

    for dia in fechas_objetivo:
        print(f"\n{dia}: descargando...")
        row = build_row(headers, dia)
        con_dato = sum(1 for v in row.values() if v is not None)
        print(f"  {con_dato}/{len(row)} columnas con dato")

        upsert_dia(db_config, dia, row)
        print(f"  Guardado en BD para {dia}")

    print(f"\nFinalizado - {len(fechas_objetivo)} dias cargados/actualizados")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------
# NOTA sobre REVISAR_EXISTENTES
# ---------------------------------------------------------------------
# Con el valor por defecto (False) los dias ya presentes se omiten sin llamar
# a la API. Consecuencia: el ON CONFLICT DO UPDATE practicamente nunca se
# dispara, y una correccion publicada por ESIOS sobre un dia ya cargado no
# entra en la BD. Se comprobo el 15/08/2026 que en las filas de junio-julio
# 2025 created_at y updated_at eran identicos al segundo, coherente con esto.
#
# Para la potencia disponible el riesgo es bajo (varia poco y se publica en
# firme), pero conviene decidirlo de forma consciente. Poner a True cuesta
# 30 dias x 7 indicadores = 210 peticiones diarias en vez de las 7 actuales.
# Una alternativa mas barata: revisar solo los ultimos 3-5 dias.
