"""
TFM Energia UCM — MIBGAS Data Loader v4 (final)
Carga el precio diario de gas natural MIBGAS desde ficheros Excel anuales.

Producto: MIBGAS-ES Index / MIBGAS Index — indice oficial consolidado España
Homogeneo para toda la serie 2020-2026.

Compatibilidad automatica por año:
  2020      : hoja 'Indices'       → columna 'MIBGAS-ES Index [EUR/MWh]'
  2021-2022 : hoja 'Indices'       → columna 'MIBGAS Index [EUR/MWh]'
  2023-2026 : hoja 'MIBGAS Indexes'→ columna 'MIBGAS-ES Index [EUR/MWh]'

La columna se detecta automaticamente en cada fichero.

Colocar ficheros en: ingesta/mibgas/MIBGAS_Data_YYYY.xlsx

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

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from config import load_config

COL_FECHA  = "Delivery day"
DB_COLUMN  = "gas_mibgas"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("mibgas_load")

# ── Helpers ────────────────────────────────────────────────────────────────────

def find_sheet(xl: pd.ExcelFile) -> str | None:
    """Detecta la hoja correcta segun el año del fichero."""
    sheets = xl.sheet_names
    if "MIBGAS Indexes" in sheets:
        return "MIBGAS Indexes"
    elif "Indices" in sheets:
        return "Indices"
    return None


def find_precio_col(df: pd.DataFrame) -> str | None:
    """
    Busca dinamicamente la columna del indice MIBGAS-ES principal.
    Excluye: LNG, AVB, VTP, PT, PVB, Last Price, Average Price.
    """
    for col in df.columns:
        col_upper = col.upper()
        if ("MIBGAS" in col_upper and "INDEX" in col_upper and
            "LNG"     not in col_upper and
            "AVB"     not in col_upper and
            "VTP"     not in col_upper and
            "-PT"     not in col_upper and
            "PVB"     not in col_upper and
            "LAST"    not in col_upper and
            "AVERAGE" not in col_upper and
            "VOLUME"  not in col_upper):
            return col
    return None


def read_mibgas_file(filepath: Path) -> pd.DataFrame | None:
    """Lee un fichero Excel MIBGAS y extrae el indice diario ES."""
    try:
        xl = pd.ExcelFile(filepath)
        sheet = find_sheet(xl)
        if sheet is None:
            log.error(f"  Hoja no encontrada en {filepath.name} — hojas: {xl.sheet_names}")
            return None

        df = pd.read_excel(filepath, sheet_name=sheet)

        # Detectar columna de precio
        col_precio = find_precio_col(df)
        if col_precio is None:
            log.error(f"  Columna de precio no encontrada en {filepath.name}")
            log.error(f"  Columnas: {df.columns.tolist()}")
            return None

        if COL_FECHA not in df.columns:
            log.error(f"  Columna 'Delivery day' no encontrada en {filepath.name}")
            return None

        log.info(f"  Hoja: '{sheet}' | Columna: '{col_precio.strip()}'")

        # Seleccionar, limpiar y filtrar
        df = df[[COL_FECHA, col_precio]].copy()
        df.columns = ["fecha", "gas_mibgas"]
        df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce").dt.date
        df["gas_mibgas"] = pd.to_numeric(df["gas_mibgas"], errors="coerce")
        df = df.dropna(subset=["fecha", "gas_mibgas"])
        df = df.drop_duplicates(subset=["fecha"], keep="first")

        log.info(f"  {filepath.name}: {len(df)} filas "
                 f"({df['fecha'].min()} → {df['fecha'].max()})")
        return df

    except Exception as e:
        log.error(f"  Error leyendo {filepath.name}: {e}")
        return None

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_existing_dates(conn, fechas: list) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT fecha FROM commodities WHERE fecha = ANY(%s)", (fechas,))
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
                UPDATE commodities SET {DB_COLUMN} = %s
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
    log.info(f"Producto: MIBGAS-ES Index — indice oficial consolidado España")

    files = sorted(folder.glob("MIBGAS_Data_*.xlsx"))
    if not files:
        log.error(f"No se encontraron ficheros MIBGAS_Data_*.xlsx en {folder}")
        return

    log.info(f"Ficheros encontrados: {len(files)}")
    total_ins, total_upd, total_skip = 0, 0, 0

    for filepath in files:
        log.info(f"\nProcesando {filepath.name}...")
        df = read_mibgas_file(filepath)
        if df is None:
            continue

        fechas     = list(df["fecha"].tolist())
        existing   = get_existing_dates(conn, fechas)
        with_nulls = get_dates_with_nulls(conn, fechas)

        new_records, update_records, skip = [], [], 0

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
    parser.add_argument("--folder", default=str(Path(__file__).parent.parent / "mibgas"))
    args = parser.parse_args()
    run(Path(args.folder))
