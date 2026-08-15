"""
TFM Energia UCM - Pipeline DIARIO de potencia INSTALADA

Descarga el valor MENSUAL de cada indicador y lo escribe en TODOS los dias del
mes. La potencia instalada es un dato mensual que ESIOS replica a diario.

CAMBIOS 15/08/2026
------------------
1. ELIMINADOS los indicadores 1479 (diesel), 1480 (turbina de gas) y 1484
   (hidroeolica). Cero dato en 2.418 dias con geo_trunc=electric_system, que
   ya reagrega las CCAA peninsulares: son tecnologias exclusivamente
   extrapeninsulares. Las columnas se eliminaron de la tabla, asi que sin este
   cambio el pipeline falla con error 42703.

2. VENTANA DE REESCRITURA DEL MES. Antes se escribia UNA sola fila, la de hoy.
   Como ESIOS publica "diariamente para el mes en curso" y revisa el valor
   durante todo el mes, los dias ya cargados se quedaban congelados con la
   primera version publicada. Resultado: un mismo mes podia tener los primeros
   dias con el valor viejo y los ultimos con el revisado, siendo el dato
   mensual UNICO por definicion. Ahora se reescribe el mes entero en cada
   ejecucion, y los 5 primeros dias del mes tambien el anterior, para
   consolidar su cierre.

3. time_agg=average anadido. Con time_trunc=month y sin time_agg, el default
   de ESIOS es SUM. Hoy no infla porque estos indicadores son nativos
   mensuales (un valor por mes, y sumar un valor lo deja igual), pero si ESIOS
   cambiara la granularidad -como hizo con el indicador 600 en enero de 2025-
   el error entraria de forma silenciosa y plausible.

4. COALESCE en el DO UPDATE. Al reescribir el mes completo en cada pase, un
   fallo puntual de la API machacaria con NULL hasta 31 dias de datos buenos.

Cron job (servidor):
    0 21 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/esios_daily_capacity_instaled.py >> /home/ubuntu/scripts/logs/cron_capacity_installed.log 2>&1
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

PENINSULA_GEO_ID = 8741

# Los 5 primeros dias del mes se reescribe tambien el mes anterior.
DIAS_CONSOLIDACION = 5

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


def primer_dia(d: date) -> date:
    return d.replace(day=1)


def ultimo_dia(d: date) -> date:
    siguiente = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return siguiente - timedelta(days=1)


def fetch_indicator_month(headers, indicator_id, mes: date):
    """Valor del indicador para el mes al que pertenece 'mes'."""
    ini = primer_dia(mes)
    fin = ultimo_dia(mes)

    try:
        resp = requests.get(
            f"https://api.esios.ree.es/indicators/{indicator_id}",
            headers=headers,
            params={
                "start_date": ini.strftime("%Y-%m-%dT00:00:00"),
                "end_date": fin.strftime("%Y-%m-%dT23:59:00"),
                "time_trunc": "month",
                # Obligatorio: sin esto el default de ESIOS es SUM.
                "time_agg": "average",
                "geo_agg": "sum",
                "geo_trunc": "electric_system",
            },
            timeout=30
        )
        if resp.status_code != 200:
            print(f"    Indicador {indicator_id}: HTTP {resp.status_code}")
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
        print(f"    Indicador {indicator_id}: ERROR {str(e)[:70]}")
        return None


def build_row(headers, mes: date) -> dict:
    """Una sola peticion por indicador y mes: el valor es mensual."""
    row = {}
    for ind_id, col in INDICATORS_INSTALLED.items():
        row[col] = fetch_indicator_month(headers, ind_id, mes)
        time.sleep(0.2)
    return row


def upsert_mes(db_config, mes: date, hasta: date, row: dict) -> int:
    """Escribe el valor mensual en todos los dias del mes hasta 'hasta'."""
    cols = list(row.keys())
    col_names = ", ".join(cols)
    # COALESCE: si un indicador fallo, no machacar el dato bueno con NULL.
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, esios_capacity_installed.{c})"
        for c in cols)
    valores = [float(row[c]) if row[c] is not None else None for c in cols]

    ini = primer_dia(mes)
    fin = min(ultimo_dia(mes), hasta)
    dias = [ini + timedelta(days=k) for k in range((fin - ini).days + 1)]
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


def meses_a_procesar(hoy: date) -> list:
    """Mes en curso siempre. Los primeros dias, tambien el anterior."""
    meses = [primer_dia(hoy)]
    if hoy.day <= DIAS_CONSOLIDACION:
        meses.insert(0, primer_dia(primer_dia(hoy) - timedelta(days=1)))
    return meses


def main():
    print(f"Pipeline diario potencia INSTALADA - {datetime.now()}")
    headers, db_config = load_config()
    hoy = date.today()

    total = 0
    for mes in meses_a_procesar(hoy):
        print(f"\nMes {mes:%Y-%m}")
        row = build_row(headers, mes)
        con_dato = sum(1 for v in row.values() if v is not None)
        print(f"  {con_dato}/{len(row)} indicadores con dato")

        if con_dato == 0:
            print("  Sin datos, no se escribe nada.")
            continue

        n = upsert_mes(db_config, mes, hoy, row)
        total += n
        print(f"  {n} dias escritos ({primer_dia(mes)} .. "
              f"{min(ultimo_dia(mes), hoy)})")

    print(f"\nFinalizado - {total} filas insertadas/actualizadas")


if __name__ == "__main__":
    main()
