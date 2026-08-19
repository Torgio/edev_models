"""
Extracción + normalización mínima del eje temporal, tabla por tabla, leyendo
directo de Postgres (nunca de CSV — el CSV solo se usó para validar la
lógica de join antes de tener acceso a la base real).

Corre independiente del script/notebook de unión: actualizar el parquet de
una tabla no implica tocar las demás ni recalcular el merge.

Uso:
    python extraccion_bronze.py                            # todas las tablas de TABLES
    python extraccion_bronze.py esios_gen                  # solo esa tabla
    python extraccion_bronze.py esios_gen entsoe_gen_data   # varias

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


def load_raw(table_name: str, conn) -> pd.DataFrame:
    return pd.read_sql(f"SELECT * FROM {table_name}", conn)


def normalize_time_axis(df: pd.DataFrame, time_col: str, prefix: str) -> pd.DataFrame:
    df = df.drop(columns=[c for c in DROP_COLS if c in df.columns])
    df = df.rename(columns={time_col: "ts_utc"})
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    rename_map = {c: f"{prefix}_{c}" for c in df.columns if c != "ts_utc"}
    return df.rename(columns=rename_map)


def extraer_tabla(table_name: str, conn) -> Path:
    cfg = TABLES[table_name]
    df = load_raw(table_name, conn)
    df = normalize_time_axis(df, cfg["time_col"], cfg["prefix"])

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