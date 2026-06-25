"""
TFM Energia UCM — MIBGAS Data Loader
Carga el precio diario de gas natural MIBGAS PVB API Day-Ahead
desde los ficheros Excel anuales descargados de mibgas.es.

Producto: MIBGAS PVB Average Price Index Day-Ahead (API_DA) en EUR/MWh
Hoja Excel: MIBGAS Indexes
Columna: MIBGAS PVB Average Price Index Day-Ahead [EUR/MWh]

Colocar los ficheros Excel en: ingesta/mibgas/
Formato nombre: MIBGAS_Data_YYYY.xlsx

Usage:
    python mibgas_load.py
    python mibgas_load.py --folder ingesta/mibgas
"""

import argparse
import logging
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MIBGAS_FOLDER    = Path(__file__).parent / "mibgas"
SHEET_NAME       = "MIBGAS Indexes"
COL_FECHA        = "Delivery day"
COL_API_DA       = "MIBGAS PVB Average Price Index Day-Ahead\n[EUR/MWh]"
DB_COLUMN        = "gas_mibgas"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("mibgas_load")

# ── Lectura Excel ──────────────────────────────────────────────────────────────

def read_mibgas_file(filepath: Path) -> pd.DataFrame | None:
    """Lee un fichero Excel de MIBGAS y devuelve DataFrame con fecha y API_DA."""
    try:
        df = pd.read_excel(filepath, sheet_name=SHEET_NAME)

        # Verificar columnas
        if COL_FECHA not in df.columns or COL_API_DA not in df.columns:
            log.error(f"  Columnas no encontradas en {filepath.name}")
            log.error(f"  Columnas disponibles: {df.columns.tolist()}")
            return None

        # Seleccionar y limpiar
        df = df[[COL_FECHA, COL_API_DA]].copy()
        df.columns = ["fecha", "gas_mibgas"]
        df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce").dt.date
        df["gas_mibgas"] = pd.to_numeric(df["gas_mibgas"], errors="coerce")
        df = df.dropna(subset=["fecha", "gas_mibgas"])

        log.info(f"  {filepath.name}: {len(df)} filas "
                 f"({df['fecha'].min()} → {df['fecha'].max()})")
        return df

    except Exception as e:
        log.error(f"  Error leyendo {filepath.name}: {e}")
        return None

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_existing_dates(conn, fechas: list) -> set:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fecha FROM commodities
            WHERE fecha = ANY(%s)
        """, (fechas,))
        return {row[0] for row in cur.fetchall()}


def get_dates_with_nulls(conn, fechas: list) -> set:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT fecha FROM commodities
            WHERE fecha = ANY(%s) AND {DB_COLUMN} IS NULL
        """, (fechas,))
        return {row[0] for row in cur.fetchall()}


def insert_rows(conn, records: list) -> int:
    if not records:
        return 0
    sql = f"""
        INSERT INTO commodities (fecha, {DB_COLUMN})
        VALUES %s
        ON CONFLICT (fecha) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def update_nulls(conn, records: list) -> int:
    if not records:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for fecha, valor in records:
            cur.execute(f"""
                UPDATE commodities
                SET {DB_COLUMN} = %s
                WHERE fecha = %s AND {DB_COLUMN} IS NULL
            """, (valor, fecha))
            if cur.rowcount > 0:
                updated += 1
    conn.commit()
    return updated

# ── Main ───────────────────────────────────────────────────────────────────────

def run(folder: Path):
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")

    # Buscar todos los ficheros Excel de MIBGAS
    files = sorted(folder.glob("MIBGAS_Data_*.xlsx"))
    if not files:
        log.error(f"No se encontraron ficheros MIBGAS_Data_*.xlsx en {folder}")
        return

    log.info(f"Ficheros encontrados: {len(files)}")

    total_ins = 0
    total_upd = 0
    total_skip = 0

    for filepath in files:
        log.info(f"\nProcesando {filepath.name}...")
        df = read_mibgas_file(filepath)
        if df is None:
            continue

        fechas = list(df["fecha"].tolist())

        # Consulta BD
        existing   = get_existing_dates(conn, fechas)
        with_nulls = get_dates_with_nulls(conn, fechas)

        new_records    = []
        update_records = []
        skip           = 0

        for _, row in df.iterrows():
            fecha = row["fecha"]
            valor = float(row["gas_mibgas"])
            if fecha not in existing:
                new_records.append((fecha, valor))
            elif fecha in with_nulls:
                update_records.append((fecha, valor))
            else:
                skip += 1

        log.info(f"  INSERT: {len(new_records)} | UPDATE: {len(update_records)} | SKIP: {skip}")

        if new_records:
            ins = insert_rows(conn, new_records)
            total_ins += ins
            log.info(f"  Inserted {ins} rows")

        if update_records:
            upd = update_nulls(conn, update_records)
            total_upd += upd
            log.info(f"  Updated {upd} rows")

        total_skip += skip

    conn.close()
    log.info(f"\nDONE: {total_ins} inserted | {total_upd} updated | {total_skip} skipped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MIBGAS Excel → PostgreSQL")
    parser.add_argument("--folder", default=str(MIBGAS_FOLDER),
                        help="Carpeta con los ficheros MIBGAS_Data_YYYY.xlsx")
    args = parser.parse_args()
    run(Path(args.folder))
