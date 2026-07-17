"""
TFM Energia UCM — MIBGAS Daily Pipeline v2
Descarga automaticamente el Excel MIBGAS desde mibgas.es,
lo guarda en disco con la fecha en el nombre y carga gas_mibgas en BD.

Estructura de archivos:
    ~/scripts/data/mibgas/MIBGAS_Data_2026_20260717.xlsx
    ~/scripts/data/mibgas/MIBGAS_Data_2026_20260718.xlsx
    ...

Producto: MIBGAS-ES Index (EUR/MWh) — indice oficial gas natural España

Cron job (servidor):
    0 17 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/mibgas_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_mibgas.log 2>&1
"""

import logging
import sys
import time
import io
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

LOGS_DIR  = Path(__file__).parent.parent / "logs"
DATA_DIR  = Path(__file__).parent / "data_mibgas_excel"
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_COLUMN = "gas_mibgas"
COL_FECHA = "Delivery day"

MIBGAS_URLS = {
    2024: "https://www.mibgas.es/es/file-access/MIBGAS_Data_2024.xlsx?path=AGNO_2024/XLS",
    2025: "https://www.mibgas.es/es/file-access/MIBGAS_Data_2025.xlsx?path=AGNO_2025/XLS",
    2026: "https://www.mibgas.es/es/file-access/MIBGAS_Data_2026.xlsx?path=AGNO_2026/XLS",
}

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    log_file = LOGS_DIR / f"mibgas_pipeline_{date.today()}.log"
    logger = logging.getLogger("mibgas_pipeline")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# ── Descarga y guardado Excel ──────────────────────────────────────────────────

def find_sheet(xl: pd.ExcelFile) -> str | None:
    sheets = xl.sheet_names
    if "MIBGAS Indexes" in sheets:
        return "MIBGAS Indexes"
    elif "Indices" in sheets:
        return "Indices"
    return None


def find_precio_col(df: pd.DataFrame) -> str | None:
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


def download_and_save(year: int, log) -> tuple[bytes | None, Path | None]:
    """
    Descarga el Excel MIBGAS del año indicado y lo guarda en disco
    con la fecha de hoy en el nombre.
    Retorna (contenido_bytes, ruta_guardada).
    """
    url = MIBGAS_URLS.get(year)
    if not url:
        log.error(f"URL no configurada para año {year}")
        return None, None

    # Nombre con fecha: MIBGAS_Data_2026_20260717.xlsx
    filename = f"MIBGAS_Data_{year}_{date.today().strftime('%Y%m%d')}.xlsx"
    filepath = DATA_DIR / filename

    log.info(f"Descargando Excel MIBGAS {year}...")
    log.info(f"  URL: {url}")

    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        content = r.content
        log.info(f"  Descargado: {len(content)/1024:.0f} KB")

        # Guardar en disco
        with open(filepath, "wb") as f:
            f.write(content)
        log.info(f"  Guardado en: {filepath}")

        return content, filepath

    except Exception as e:
        log.error(f"  Error descargando: {e}")
        return None, None


def parse_excel(content: bytes, log) -> pd.DataFrame | None:
    """Parsea el contenido del Excel y devuelve DataFrame limpio."""
    try:
        xl    = pd.ExcelFile(io.BytesIO(content))
        sheet = find_sheet(xl)
        if not sheet:
            log.error(f"  Hoja no encontrada. Hojas: {xl.sheet_names}")
            return None

        df = pd.read_excel(io.BytesIO(content), sheet_name=sheet)
        col_precio = find_precio_col(df)
        if not col_precio:
            log.error(f"  Columna precio no encontrada. Columnas: {df.columns.tolist()}")
            return None

        log.info(f"  Hoja: '{sheet}' | Columna: '{col_precio.strip()}'")

        df = df[[COL_FECHA, col_precio]].copy()
        df.columns = ["fecha", DB_COLUMN]
        df["fecha"]   = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce").dt.date
        df[DB_COLUMN] = pd.to_numeric(df[DB_COLUMN], errors="coerce").round(3)
        df = df.dropna(subset=["fecha", DB_COLUMN])
        df = df.drop_duplicates(subset=["fecha"], keep="first")

        log.info(f"  {len(df)} filas ({df['fecha'].min()} → {df['fecha'].max()})")
        return df

    except Exception as e:
        log.error(f"  Error procesando Excel: {e}")
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
        VALUES %s ON CONFLICT (fecha) DO NOTHING
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


def log_pipeline_db(conn, ins, upd, status, mensaje, duracion, log):
    try:
        hoy = date.today()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, ("mibgas_daily", hoy, hoy, ins + upd, status, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    log = setup_logger()
    hoy = date.today()

    log.info("=" * 55)
    log.info(f"MIBGAS Pipeline v2 — {hoy}")
    log.info(f"Directorio datos: {DATA_DIR}")
    log.info("=" * 55)

    t0 = time.time()
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    total_ins = total_upd = 0

    # Descargar año actual (y enero también descarga año anterior)
    anyos = [hoy.year]
    if hoy.month == 1:
        anyos.append(hoy.year - 1)

    for year in anyos:
        log.info(f"\n--- Año {year} ---")

        content, filepath = download_and_save(year, log)
        if content is None:
            continue

        df = parse_excel(content, log)
        if df is None:
            continue

        # Filtrar: año actual solo ultimos 30 dias, año anterior completo
        if year == hoy.year:
            df = df[df["fecha"] >= hoy - timedelta(days=30)]

        fechas     = list(df["fecha"].tolist())
        existing   = get_existing_dates(conn, fechas)
        with_nulls = get_dates_with_nulls(conn, fechas)

        new_records, update_records, skip = [], [], 0

        for _, row in df.iterrows():
            fecha = row["fecha"]
            valor = float(row[DB_COLUMN])
            if fecha not in existing:
                new_records.append((fecha, valor))
            elif fecha in with_nulls:
                update_records.append((fecha, valor))
            else:
                skip += 1

        log.info(f"  INSERT: {len(new_records)} | UPDATE: {len(update_records)} | SKIP: {skip}")

        if new_records:
            n = insert_rows(conn, new_records)
            total_ins += n
            log.info(f"  Insertadas: {n} filas")

        if update_records:
            n = update_nulls(conn, update_records)
            total_upd += n
            log.info(f"  Actualizadas: {n} filas")

    duracion = time.time() - t0
    mensaje  = f"{total_ins} insert, {total_upd} update"
    log_pipeline_db(conn, total_ins, total_upd, "ok", mensaje, duracion, log)
    conn.close()

    log.info(f"\n{'='*55}")
    log.info(f"DONE: {total_ins} insertadas | {total_upd} actualizadas | {duracion:.1f}s")
    log.info(f"Excels guardados en: {DATA_DIR}")
    log.info(f"{'='*55}")


if __name__ == "__main__":
    run()
