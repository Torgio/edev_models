"""
Carga de spot_price (ESIOS + ENTSO-E + OMIE)
Descarga las 3 fuentes para un dia concreto, filtra estrictamente a las
horas de ese dia en Madrid, y hace UPSERT en la tabla spot_price.

El campo 'datetime' se guarda como timestamptz (instante real, sin
ambiguedad en cambios de horario) y se muestra/imprime en hora de Madrid.
"""

import sys
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

sys.path.append(str(Path(__file__).parent))
from config import load_config

TZ_MADRID = ZoneInfo("Europe/Madrid")


# ── Helper: filtrar estrictamente al dia en Madrid ────────────────────────────

def filtrar_dia_madrid(df: pd.DataFrame, fecha: date, col_datetime="datetime") -> pd.DataFrame:
    """Filtra un DataFrame para quedarnos SOLO con las horas del dia (en Madrid)."""
    if df is None or df.empty:
        return df
    df = df.copy()
    df[col_datetime] = pd.to_datetime(df[col_datetime], utc=True)
    df["_local"] = df[col_datetime].dt.tz_convert("Europe/Madrid")
    df = df[df["_local"].dt.date == fecha]
    return df.drop(columns=["_local"])


# ── OMIE ─────────────────────────────────────────────────────────────────────

def descargar_omie_dia(fecha: date, intento_max: int = 3):
    """
    Descarga el precio OMIE para un dia (hora local espanola), agregando
    a horario si viene en cuartos de hora (MTU15, desde oct-2025).
    """
    fecha_str = fecha.strftime("%Y%m%d")
    resp = None
    for sufijo in range(1, intento_max + 1):
        url = f"https://www.omie.es/en/file-download?parents=marginalpdbc&filename=marginalpdbc_{fecha_str}.{sufijo}"
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and len(r.text.strip()) > 10:
                resp = r
                break
        except Exception as e:
            print(f"    Error descargando {fecha} sufijo .{sufijo}: {e}")

    if resp is None:
        print(f"  OMIE: no se encontro archivo para {fecha}")
        return None

    filas = []
    for linea in resp.text.strip().splitlines():
        partes = linea.split(";")
        if len(partes) >= 5 and partes[0].strip().isdigit():
            periodo = int(partes[3])
            precio = float(partes[4].replace(",", "."))
            filas.append({"periodo": periodo, "omie_price": precio})

    if not filas:
        print(f"  OMIE: archivo vacio/sin datos para {fecha}")
        return None

    df = pd.DataFrame(filas)
    n_periodos = df["periodo"].nunique()
    minutos_por_periodo = 15 if n_periodos > 30 else 60
    print(f"  OMIE: {n_periodos} periodos detectados ({minutos_por_periodo} min/periodo)")

    df["hora_secuencial"] = (df["periodo"] - 1) // (60 // minutos_por_periodo)
    df_horario = df.groupby("hora_secuencial", as_index=False)["omie_price"].mean()

    # Construir datetime avanzando por incrementos de 1h reales desde
    # medianoche local, dejando que ZoneInfo/fold gestionen el dia de 23/25h
    filas_out = []
    for _, row in df_horario.iterrows():
        h = int(row["hora_secuencial"])
        dt = datetime(fecha.year, fecha.month, fecha.day, tzinfo=TZ_MADRID) + timedelta(hours=h)
        filas_out.append({"datetime": dt, "omie_price": round(float(row["omie_price"]), 2)})

    return pd.DataFrame(filas_out)


# ── ESIOS (indicador 600, con correccion factor 4 MTU15 desde oct-2025) ───────

def descargar_esios_dia(fecha: date, headers: dict):
    start = fecha.strftime("%Y-%m-%dT00:00:00")
    end = fecha.strftime("%Y-%m-%dT23:59:59")

    try:
        resp = requests.get(
            "https://api.esios.ree.es/indicators/600",
            headers=headers,
            params={
                "start_date": start,
                "end_date": end,
                "time_trunc": "hour",
                "geo_ids[]": 3,
            },
            timeout=30,
        )
    except Exception as e:
        print(f"  ESIOS: ERROR — {e}")
        return None

    if resp.status_code != 200:
        print(f"  ESIOS: ERROR HTTP {resp.status_code}")
        return None

    values = resp.json().get("indicator", {}).get("values", [])
    if not values:
        print(f"  ESIOS: sin datos para {fecha}")
        return None

    df = pd.DataFrame(values)[["datetime", "value"]]
    df["datetime"] = pd.to_datetime(df["datetime"])

    # Correccion factor 4 (bug MTU15) para fechas >= 2025-10-01
    if fecha >= date(2025, 10, 1):
        df["value"] = df["value"] / 4

    df = df.rename(columns={"value": "esios_price"})
    df["esios_price"] = df["esios_price"].round(2)
    return df[["datetime", "esios_price"]]


# ── ENTSO-E ────────────────────────────────────────────────────────────────────

def descargar_entsoe_dia(fecha: date, entsoe_token: str):
    client = EntsoePandasClient(api_key=entsoe_token)
    ts_start = pd.Timestamp(str(fecha), tz="Europe/Madrid")
    ts_end = ts_start + pd.Timedelta(days=1)

    try:
        serie = client.query_day_ahead_prices("ES", start=ts_start, end=ts_end)
    except Exception as e:
        print(f"  ENTSO-E: ERROR — {e}")
        return None

    if serie.empty:
        print(f"  ENTSO-E: sin datos para {fecha}")
        return None

    serie_horaria = serie.resample("h").mean()
    df = serie_horaria.reset_index()
    df.columns = ["datetime", "entsoe_price"]
    df["entsoe_price"] = df["entsoe_price"].round(2)
    return df


# ── Combinar las 3 fuentes ─────────────────────────────────────────────────────

def combinar_fuentes(fecha: date) -> pd.DataFrame:
    headers, _ = load_config()
    creds = json.load(open(Path(__file__).parent / "credentials.json"))

    print("1. OMIE...")
    df_omie = filtrar_dia_madrid(descargar_omie_dia(fecha), fecha)

    print("2. ESIOS...")
    df_esios = filtrar_dia_madrid(descargar_esios_dia(fecha, headers), fecha)

    print("3. ENTSO-E...")
    df_entsoe = filtrar_dia_madrid(descargar_entsoe_dia(fecha, creds["entsoe_token"]), fecha)

    tabla = None
    for df in [df_esios, df_entsoe, df_omie]:
        if df is None:
            continue
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        tabla = df if tabla is None else tabla.merge(df, on="datetime", how="outer")

    if tabla is None:
        return None

    tabla = tabla.sort_values("datetime").reset_index(drop=True)
    tabla["datetime"] = tabla["datetime"].dt.tz_convert("Europe/Madrid")
    return tabla


# ── Carga a Postgres ───────────────────────────────────────────────────────────

def upsert_spot_price(db_config, df: pd.DataFrame) -> int:
    """UPSERT en spot_price. Solo actualiza las columnas presentes en df."""
    if df is None or df.empty:
        return 0

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()

    cols = [c for c in ["esios_price", "entsoe_price", "omie_price"] if c in df.columns]
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])

    rows = []
    for _, row in df.iterrows():
        valores = [float(row[c]) if pd.notna(row.get(c)) else None for c in cols]
        rows.append([row["datetime"]] + valores)

    sql = f"""
        INSERT INTO spot_price (datetime, {col_names})
        VALUES %s
        ON CONFLICT (datetime) DO UPDATE SET {updates}
    """
    template = "(" + ", ".join(["%s"] * (len(cols) + 1)) + ")"

    execute_values(cur, sql, rows, template=template)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


# ── Main ───────────────────────────────────────────────────────────────────────

def run(fecha: date):
    print(f"Cargando spot_price para {fecha}...\n")

    _, db_config = load_config()
    tabla = combinar_fuentes(fecha)

    if tabla is None:
        print("\nNo se obtuvo ningun dato. Abortando.")
        return

    print(f"\n{len(tabla)} filas a cargar:")
    print(tabla.to_string(index=False))

    n = upsert_spot_price(db_config, tabla)
    print(f"\n{n} filas insertadas/actualizadas en spot_price")


if __name__ == "__main__":
    # Ajusta la fecha segun necesites
    run(date(2026, 7, 13))