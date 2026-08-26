"""
Extracción + normalización mínima del eje temporal, tabla por tabla, leyendo
directo de Postgres (nunca de CSV — el CSV solo se usó para validar la
lógica de join antes de tener acceso a la base real).

Trae SOLO las columnas configuradas en bronze_config.TABLES (más time_col) --
no "SELECT *" y descarte posterior. Menos tráfico de red, y la lista de
columnas queda documentada en un solo lugar (ver docs/decisiones_datos.md
para el motivo de cada columna elegida).

Corre independiente del script/notebook de unión: actualizar el parquet de
una tabla no implica tocar las demás ni recalcular el merge.

Uso:
    python extract_bronze.py                            # todas las tablas de TABLES
    python extract_bronze.py esios_gen                  # solo esa tabla
    python extract_bronze.py esios_gen entsoe_gen_data   # varias

Conexión a Postgres: mismo patrón que ingesta/historic_load/era5_load.py
(config.load_config() -> psycopg2.connect(**db_config)). Si tu config.py no
vive dentro de ingesta/, ajustá la línea de sys.path más abajo.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "ingesta"))  # ajustar si config.py vive en otro lado
from config import load_config  # noqa: E402  (import tras el sys.path.append, a propósito)

from bronze_config import BRONZE_DIR, DROP_COLS, TABLES, raw_path


def get_connection():
    _, db_config = load_config()
    return psycopg2.connect(**db_config)


def load_raw(table_name: str, conn, columns: list[str] | None, time_col: str) -> pd.DataFrame:
    """SELECT solo de las columnas configuradas + time_col. Si "columns" es None
    (tabla sin lista explícita todavía), trae todo -- comportamiento anterior,
    para no romper una tabla que aún no pasó por la matriz de decisiones."""
    if columns:
        cols_sql = ", ".join([time_col] + columns)
        query = f"SELECT {cols_sql} FROM {table_name}"
    else:
        query = f"SELECT * FROM {table_name}"
    return pd.read_sql(query, conn)


def normalize_time_axis(df: pd.DataFrame, time_col: str, prefix: str, grain: str = "hourly") -> pd.DataFrame:
    """Normaliza el eje temporal según la granularidad de la tabla:
      - hourly / 3h: columna de tiempo -> ts_utc, tz-aware. La diferencia entre
        hourly y 3h no cambia esta función -- se resuelve al unir (01_union_bronze.ipynb),
        no acá.
      - daily: columna de tiempo -> date_local (fecha, sin hora). Estas tablas se
        difunden sobre 24h al unir, no se buscan por instante exacto.
    """
    df = df.drop(columns=[c for c in DROP_COLS if c in df.columns])

    if grain == "daily":
        df = df.rename(columns={time_col: "date_local"})
        # La columna puede venir como timestamp with time zone (no una fecha pura) --
        # con offsets mixtos por el cambio de horario (+01 invierno / +02 verano) a lo
        # largo del año. Sin utc=True, pd.to_datetime no puede resolver esa mezcla.
        # Se resuelve como instante UTC real y luego se toma la fecha en hora local de
        # Madrid, coherente con el date_local del calendario.
        ts_temp = pd.to_datetime(df["date_local"], utc=True)
        df["date_local"] = ts_temp.dt.tz_convert("Europe/Madrid").dt.date
        key_col = "date_local"
    else:
        df = df.rename(columns={time_col: "ts_utc"})
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        key_col = "ts_utc"

    rename_map = {c: f"{prefix}_{c}" for c in df.columns if c != key_col}
    return df.rename(columns=rename_map)


def extraer_tabla(table_name: str, conn) -> Path:
    cfg = TABLES[table_name]
    df = load_raw(table_name, conn, cfg.get("columns"), cfg["time_col"])
    df = normalize_time_axis(df, cfg["time_col"], cfg["prefix"], cfg.get("grain", "hourly"))

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    path = raw_path(table_name)
    df.to_parquet(path, index=False)
    print(f"{path} — {df.shape[0]} filas x {df.shape[1]} columnas")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extrae y normaliza tablas desde Postgres hacia la capa bronze, una por una."
    )
    parser.add_argument(
        "tablas", nargs="*",
        help="Nombres de tabla a actualizar (por defecto: todas las registradas en TABLES)",
    )
    args = parser.parse_args()

    objetivo = args.tablas or list(TABLES.keys())
    conn = get_connection()
    try:
        for nombre in objetivo:
            if nombre not in TABLES:
                print(f"Aviso: '{nombre}' no está registrada en TABLES, se omite.")
                continue
            extraer_tabla(nombre, conn)
    finally:
        conn.close()