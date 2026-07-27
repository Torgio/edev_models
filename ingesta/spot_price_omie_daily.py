"""
TFM Energia UCM — Carga diaria de precio OMIE en spot_price
1) Backfill: ultimos 7 dias, una sola llamada por dia si falta.
2) Objetivo principal: precio de MANANA (D+1). Si ya esta completo, se
   omite sin llamar a la API. Si no, reintenta cada 15 min hasta 3h.

Cron job (servidor, hora Madrid via CRON_TZ):
    CRON_TZ=Europe/Madrid
    45 13 * * * .../spot_price_omie_daily.py >> .../cron_spot_omie.log 2>&1
"""

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

TZ_MADRID = ZoneInfo("Europe/Madrid")
MAX_HORAS_REINTENTO = 3
PAUSA_REINTENTO_MIN = 15
DIAS_BACKFILL = 7
COLUMNA = "omie_price"
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


def intentar_descargar_omie(fecha: date, intento_max_sufijo: int = 3):
    fecha_str = fecha.strftime("%Y%m%d")
    resp = None

    for sufijo in range(1, intento_max_sufijo + 1):
        try:
            r = requests.get(
                f"https://www.omie.es/en/file-download?parents=marginalpdbc&filename=marginalpdbc_{fecha_str}.{sufijo}",
                timeout=30
            )
            if r.status_code == 200 and len(r.text.strip()) > 10:
                resp = r
                break
        except Exception as e:
            print(f"    Error sufijo .{sufijo}: {e}")

    if resp is None:
        print(f"    No se encontro archivo para {fecha}")
        return None

    filas = []
    for linea in resp.text.strip().splitlines():
        partes = linea.split(";")
        if len(partes) >= 5 and partes[0].strip().isdigit():
            periodo = int(partes[3])
            precio = float(partes[4].replace(",", "."))
            filas.append({"periodo": periodo, "omie_price": precio})

    if not filas:
        print(f"    Archivo vacio para {fecha}")
        return None

    df = pd.DataFrame(filas)
    n_periodos = df["periodo"].nunique()
    if n_periodos < HORAS_ESPERADAS:
        print(f"    Datos incompletos ({n_periodos} periodos)")
        return None

    minutos_por_periodo = 15 if n_periodos > 30 else 60
    df["hora_secuencial"] = (df["periodo"] - 1) // (60 // minutos_por_periodo)
    df_horario = df.groupby("hora_secuencial", as_index=False)["omie_price"].mean()

    filas_out = []
    for _, row in df_horario.iterrows():
        h = int(row["hora_secuencial"])
        dt = datetime(fecha.year, fecha.month, fecha.day, tzinfo=TZ_MADRID) + timedelta(hours=h)
        filas_out.append({"datetime": dt, "omie_price": round(float(row["omie_price"]), 2)})

    return pd.DataFrame(filas_out)


def descargar_con_reintentos(fecha: date):
    max_intentos = int((MAX_HORAS_REINTENTO * 60) / PAUSA_REINTENTO_MIN)
    inicio = datetime.now()

    for intento in range(1, max_intentos + 1):
        transcurrido = (datetime.now() - inicio).total_seconds() / 60
        print(f"  Intento {intento}/{max_intentos} (t+{transcurrido:.0f} min)...")

        df = intentar_descargar_omie(fecha)
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
    print(f"Pipeline diario spot_price — OMIE")
    print(f"Inicio: {datetime.now()}\n")

    _, db_config = load_config()

    print(f"--- Backfill (ultimos {DIAS_BACKFILL} dias) ---")
    incompletos = dias_incompletos_recientes(db_config)

    if not incompletos:
        print("  Todos los dias recientes estan completos.\n")
    else:
        for fecha_f, count_actual in incompletos:
            print(f"  {fecha_f}: {count_actual}/{HORAS_ESPERADAS}+ horas -> reintentando (1 llamada)...")
            df = intentar_descargar_omie(fecha_f)
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

    df = descargar_con_reintentos(fecha_manana)
    df = filtrar_dia_madrid(df, fecha_manana)

    if df is None or df.empty:
        print(f"\nNo se pudieron obtener datos para {fecha_manana}")
        return

    n = upsert_precio(db_config, df)
    print(f"\n{n} filas insertadas/actualizadas para {fecha_manana}")


if __name__ == "__main__":
    main()