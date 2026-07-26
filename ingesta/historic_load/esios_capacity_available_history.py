"""
TFM Energia UCM — Carga historica de potencia DISPONIBLE (2020-actualidad)
Descarga y carga AÑO A AÑO, automaticamente sin confirmacion manual.
Antes de descargar cada bloque, revisa si ya esta cargado en BD para no
repetir llamadas innecesarias a la API (aunque el upsert ya es idempotente).
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


def rango_anual(start: date, end: date):
    tramos = []
    y = start.year
    while y <= end.year:
        tramo_ini = date(y, 1, 1) if y > start.year else start
        tramo_fin = date(y, 12, 31) if y < end.year else end
        tramos.append((tramo_ini, tramo_fin))
        y += 1
    return tramos


def dias_ya_cargados(db_config, tramo_ini: date, tramo_fin: date) -> int:
    """Cuenta cuantas fechas ya existen en la tabla para este tramo."""
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM esios_capacity_available WHERE date BETWEEN %s AND %s",
        (tramo_ini, tramo_fin)
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def fetch_indicator_chunk(headers, indicator_id, start: date, end: date, reintentos=2):
    start_str = start.strftime("%Y-%m-%dT00:00:00")
    end_str = end.strftime("%Y-%m-%dT23:00:00")

    for intento in range(reintentos + 1):
        try:
            resp = requests.get(
                f"https://api.esios.ree.es/indicators/{indicator_id}",
                headers=headers,
                params={
                    "start_date": start_str,
                    "end_date": end_str,
                    "time_trunc": "day",
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

            peninsula["fecha"] = pd.to_datetime(peninsula["datetime"], utc=True).dt.date
            return peninsula[["fecha", "value"]]

        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}, intento {intento+1}")
            if intento < reintentos:
                time.sleep(3)
                continue
            return None

    return None


def descargar_bloque(headers, tramo_ini: date, tramo_fin: date) -> pd.DataFrame:
    series = {}
    for ind_id, col in INDICATORS_AVAILABLE.items():
        df_serie = fetch_indicator_chunk(headers, ind_id, tramo_ini, tramo_fin)
        if df_serie is not None:
            series[col] = df_serie.set_index("fecha")["value"]
        time.sleep(0.4)

    if not series:
        return pd.DataFrame()

    df_bloque = pd.DataFrame(series)

    coal_cols = [c for c in ["coal_antracita_mw", "coal_subbituminosa_mw"] if c in df_bloque.columns]
    if coal_cols:
        df_bloque["coal_mw"] = df_bloque[coal_cols].sum(axis=1, skipna=True)
        df_bloque = df_bloque.drop(columns=coal_cols)

    df_bloque.index.name = "fecha"
    return df_bloque.sort_index()


def upsert_bloque(db_config, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    cols = list(df.columns)
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()

    rows = []
    for fecha, row in df.iterrows():
        valores = [float(row[c]) if pd.notna(row[c]) else None for c in cols]
        rows.append([fecha] + valores)

    sql = f"""
        INSERT INTO esios_capacity_available (date, {col_names}, updated_at)
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET
            {updates},
            updated_at = now()
    """
    template = "(" + ", ".join(["%s"] * (len(cols) + 1)) + ", now())"

    execute_values(cur, sql, rows, template=template, page_size=200)
    conn.commit()
    cur.close()
    conn.close()

    return len(rows)


def main():
    start = date(START_YEAR, START_MONTH, 1)
    ultimo_dia = calendar.monthrange(END_YEAR, END_MONTH)[1]
    end = date(END_YEAR, END_MONTH, ultimo_dia)

    print(f"Cargando historico de potencia DISPONIBLE: {start} a {end}")
    print("(automatico, por bloques anuales, sin repetir lo ya cargado)\n")

    headers, db_config = load_config()
    tramos = rango_anual(start, end)
    total_filas = 0

    for tramo_ini, tramo_fin in tramos:
        dias_esperados = (tramo_fin - tramo_ini).days + 1

        print(f"\n{'='*60}")
        print(f"BLOQUE {tramo_ini.year} — {tramo_ini} a {tramo_fin}")
        print(f"{'='*60}")

        ya_cargados = dias_ya_cargados(db_config, tramo_ini, tramo_fin)
        print(f"  Dias ya en BD para este tramo: {ya_cargados}/{dias_esperados}")

        if ya_cargados >= dias_esperados:
            print(f"  Bloque {tramo_ini.year} ya esta completo en BD. Se omite (sin llamar a la API).")
            continue

        df_bloque = descargar_bloque(headers, tramo_ini, tramo_fin)

        if df_bloque.empty:
            print(f"  Sin datos descargados para {tramo_ini.year}, se omite.")
            continue

        print(f"  {len(df_bloque)} filas descargadas para {tramo_ini.year}")
        print(df_bloque.describe().to_string())

        backup_file = f"backup_disponible_{tramo_ini.year}.csv"
        df_bloque.to_csv(backup_file)
        print(f"  Backup guardado en {backup_file}")

        filas = upsert_bloque(db_config, df_bloque)
        total_filas += filas
        print(f"  {filas} filas cargadas/actualizadas en BD para {tramo_ini.year}.")

    print(f"\n{'='*60}")
    print(f"CARGA FINALIZADA — Total filas cargadas/actualizadas: {total_filas}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()