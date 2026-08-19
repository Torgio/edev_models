"""Configuración compartida entre extracción y unión de la capa bronze.
Vive en scripts/ — se importa desde extraccion_bronze.py y desde
notebooks/02_union_bronze.ipynb, para que no haya drift entre lo que uno
escribe y lo que el otro espera leer.
"""
from pathlib import Path

# Rutas ancladas a la posición de este archivo, no al cwd desde donde se
# ejecute el script o el notebook.
REPO_ROOT = Path(__file__).resolve().parent.parent
BRONZE_DIR = REPO_ROOT / "data" / "bronze"

TZ_LOCAL = "Europe/Madrid"

# Tabla que define el rango del calendario: la más limpia / con histórico más
# largo y confiable (ver auditoría: 2020->hoy, sin huecos conocidos).
ANCHOR_TABLE = "entsoe_gen_data"

DROP_COLS = ["updated_at"]

# Agregar una tabla nueva = una línea acá. Ni extraccion_bronze.py ni
# 02_union_bronze.ipynb necesitan tocarse.
TABLES = {
    "entsoe_gen_data": {"time_col": "datetime_utc", "prefix": "entsoe"},
    "esios_gen":        {"time_col": "datetime",     "prefix": "esios_gen"},
}

# Convención de nombres de archivo -- definida una sola vez acá para que
# extraccion_bronze.py (que escribe) y 02_union_bronze.ipynb (que lee) no
# puedan desincronizarse.
RAW_SUFFIX = "_raw"
UNIFIED_FILENAME = "bronze_unificado.parquet"


def raw_path(table_name: str) -> Path:
    """Ruta del parquet bronze de una tabla, ej. entsoe_gen_data -> entsoe_gen_data_raw.parquet"""
    return BRONZE_DIR / f"{table_name}{RAW_SUFFIX}.parquet"