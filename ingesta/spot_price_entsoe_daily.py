"""
TFM Energia UCM — Carga diaria de precio ENTSO-E en spot_price
1) Backfill: ultimos 7 dias, una sola llamada por dia si falta.
2) Objetivo principal: precio de MANANA (D+1). Si ya esta completo, se
   omite sin llamar a la API. Si no, reintenta cada 15 min hasta 3h.

Cron job (servidor, hora Madrid via CRON_TZ):
    CRON_TZ=Europe/Madrid
    0 14 * * * .../spot_price_entsoe_daily.py >> .../cron_spot_entsoe.log 2>&1
"""

import sys
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

sys.path.append(str(Path(__file__).parent))
from config import load_config

MAX_HORAS_REINTENTO = 3
PAUSA_REINTENTO_MIN = 15
DIAS_BACKFILL = 7
COLUMNA = "entsoe_price"
HORAS_ESPERADAS = 23


def filtrar_dia_madrid(df: pd.DataFrame, fecha: date, col_datetime="datetime") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df[col_datetime] = pd.to_datetime(df[col_datetime], utc=True)
    df["_local"] = df[col_datetime].dt.tz_convert("Europe/Madrid")
    df = df[df["_local"].dt.date == fecha]
    return df.drop(columns=["_local"])


def dia_ya_completo(db_config, fecha: date, columna: str = COLUMNA, horas_esperadas: int = HORAS_ESPERADAS) -> bool:
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM spot_price WHERE datetime::date = %s AND {columna} IS NOT NULL",
        (fecha,)
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count >= horas_esperadas


def intentar_descargar_entsoe(fecha: date, entsoe_token: str):
    client = EntsoePandasClient(api_key=entsoe_token)
    ts_start = pd.Timestamp(str(fecha), tz="Europe/Madrid")
    ts_end = ts_start + pd.Timedelta(days=1)

    try:
        serie = client.query_day_ahead_prices("ES", start=ts_start, end=ts_end)
    except Exception as e:
        print(f"    ERROR: {e}")
        return None

    if serie.empty or len(serie) < HORAS_ESPERADAS:
        print(f"    Datos incompletos ({len(serie)} valores)")
        return None

    serie_horaria = serie.resample("h").mean()
    df = serie_horaria.reset_index()
    df.columns = ["datetime", "entsoe_price"]
    df["entsoe_price"] = df["entsoe_price"].round(2)
    return df


def descargar_con_reintentos(fecha: date, entsoe_token: str):
    max_intentos = int((MAX_HORAS_REINTENTO * 60) / PAUSA_REINTENTO_MIN)
    inicio = datetime.now()

    for intento in range(1, max_intentos + 1):
        transcurrido = (datetime.now() - inicio).total_seconds() / 60
        print(f"  Intento {intento}/{max_intentos} (t+{transcurrido:.0f} min)...")

        df = intentar_descargar_entsoe(fecha, entsoe_token)
        if df is not None:
            print(f"  Datos obtenidos en el intento {intento}")
            return df

        if intento < max_intentos:
            print(f"    Esperando {PAUSA_REINTENTO_MIN} min...")
            time.sleep(PAUSA_REINTENTO_MIN * 60)

    print(f"  AGOTADOS los reintentos ({MAX_HORAS_REINTENTO}h) para {fecha}")
    return None


def dias_incompletos_recientes(db_config, dias_atras: int = DIAS_BACKFILL):
    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(1, dias_atras + 1)]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    incompletos = []
    for f in fechas:
        cur.execute(
            f"SELECT COUNT(*) FROM spot_price WHERE datetime::date = %s AND {COLUMNA} IS NOT NULL",
            (f,)
        )
        count = cur.fetchone()[0]
        if count < HORAS_ESPERADAS:
            incompletos.append((f, count))
    cur.close()
    conn.close()
    return incompletos


def upsert_precio(db_config, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    rows = [(row["datetime"], float(row[COLUMNA])) for _, row in df.iterrows()]
    sql = f"""
        INSERT INTO spot_price (datetime, {COLUMNA})
        VALUES %s
        ON CONFLICT (datetime) DO UPDATE SET {COLUMNA} = EXCLUDED.{COLUMNA}
    """
    execute_values(cur, sql, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


def main():
    print(f"Pipeline diario spot_price — ENTSO-E")
    print(f"Inicio: {datetime.now()}\n")

    _, db_config = load_config()
    creds = json.load(open(Path(__file__).parent / "credentials.json"))
    token = creds["entsoe_token"]

    print(f"--- Backfill (ultimos {DIAS_BACKFILL} dias) ---")
    incompletos = dias_incompletos_recientes(db_config)

    if not incompletos:
        print("  Todos los dias recientes estan completos.\n")
    else:
        for fecha_f, count_actual in incompletos:
            print(f"  {fecha_f}: {count_actual}/{HORAS_ESPERADAS}+ horas -> reintentando (1 llamada)...")
            df = intentar_descargar_entsoe(fecha_f, token)
            df = filtrar_dia_madrid(df, fecha_f)
            if df is not None and not df.empty:
                n = upsert_precio(db_config, df)
                print(f"    {n} filas actualizadas para {fecha_f}")
            else:
                print(f"    Sigue sin datos para {fecha_f}, se reintentara mañana")
        print()

    fecha_manana = date.today() + timedelta(days=1)
    print(f"--- Objetivo principal: {fecha_manana} ---")

    if dia_ya_completo(db_config, fecha_manana):
        print(f"  {fecha_manana} ya esta completo ({COLUMNA}). Nada que hacer.")
        return

    df = descargar_con_reintentos(fecha_manana, token)
    df = filtrar_dia_madrid(df, fecha_manana)

    if df is None or df.empty:
        print(f"\nNo se pudieron obtener datos para {fecha_manana}")
        return

    n = upsert_precio(db_config, df)
    print(f"\n{n} filas insertadas/actualizadas para {fecha_manana}")


if __name__ == "__main__":
    main()