"""
TFM Energia UCM — ENTSO-E Data Loader
Descarga datos reales de generacion, carga y flujos de interconexion
desde la API de ENTSO-E y los carga en la tabla entsoe_data.

Logica anti-duplicados:
  - Consulta BD antes de descargar
  - INSERT solo timestamps nuevos
  - UPDATE columnas NULL en timestamps existentes
  - ON CONFLICT DO NOTHING como doble proteccion

Granularidad: cuarthoraria → agregada a horaria con resample('h').mean()

Usage:
    python entsoe_data_load.py
"""

import logging
import time
from datetime import date, timedelta, datetime, timezone

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

from config import load_config

# ╔══════════════════════════════════════════════════════════════╗
# ║            CONFIGURACION DE CARGA — EDITAR AQUI             ║
# ╚══════════════════════════════════════════════════════════════╝

MODE       = "range"         # "test" | "range" | "yesterday"

START_DATE = "2026-06-01"   # Inicio (usado en: range / test)
END_DATE   = "2026-06-25"   # Fin    (usado en: range)

# ╚══════════════════════════════════════════════════════════════╝
# ║                  FIN DE CONFIGURACION                       ║
# ╚══════════════════════════════════════════════════════════════╝

COUNTRY    = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"
TIMEZONE   = "Europe/Madrid"
CHUNK_DAYS = 7
PAUSE_SEC  = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("entsoe_data_load")

# ── Mapeo generacion ENTSO-E → columnas BD ─────────────────────────────────────

GEN_MAPPING = {
    "solar_mw":                 [("Solar", "Actual Aggregated")],
    "wind_mw":                  [("Wind Onshore", "Actual Aggregated")],
    "nuclear_mw":               [("Nuclear", "Actual Aggregated")],
    "ccgt_mw":                  [("Fossil Gas", "Actual Aggregated")],
    "coal_mw":                  [("Fossil Hard coal", "Actual Aggregated")],
    "biomass_mw":               [("Biomass", "Actual Aggregated")],
    "waste_mw":                 [("Waste", "Actual Aggregated")],
    "other_generation_mw":      [("Other", "Actual Aggregated"),
                                  ("Other renewable", "Actual Aggregated")],
    "hydro_mw":                 [("Hydro Water Reservoir", "Actual Aggregated"),
                                  ("Hydro Run-of-river and poundage", "Actual Aggregated")],
    "pumping_generation_mw":    [("Hydro Pumped Storage", "Actual Aggregated"),
                                  ("Energy storage", "Actual Aggregated")],
    "pumping_consumption_mw":   [("Hydro Pumped Storage", "Actual Consumption"),
                                  ("Energy storage", "Actual Consumption")],
    "cogeneration_mw":          [("Fossil Oil", "Actual Aggregated")],
}

ALL_COLS = [
    "datetime_utc", "datetime_local",
    "actual_load_mw",
    "solar_mw", "wind_mw", "nuclear_mw", "ccgt_mw", "coal_mw",
    "biomass_mw", "waste_mw", "hydro_mw", "cogeneration_mw",
    "other_generation_mw", "pumping_generation_mw", "pumping_consumption_mw",
    "renewable_generation_mw", "thermal_generation_mw",
    "residual_demand_mw", "net_load_mw",
    "flow_es_fr_mw", "flow_fr_es_mw", "net_flow_fr_mw",
    "flow_es_pt_mw", "flow_pt_es_mw", "net_flow_pt_mw",
    "net_flow_total_mw",
]

# ── Fecha resolution ───────────────────────────────────────────────────────────

def resolve_dates() -> tuple[date, date]:
    if MODE == "yesterday":
        d = date.today() - timedelta(days=1)
        return d, d
    elif MODE == "test":
        start = date.fromisoformat(START_DATE)
        return start, start + timedelta(days=6)
    elif MODE == "range":
        return date.fromisoformat(START_DATE), date.fromisoformat(END_DATE)
    else:
        raise ValueError(f"Unknown MODE: {MODE}. Use: test | range | yesterday")

# ── ENTSO-E fetch ──────────────────────────────────────────────────────────────

def to_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(str(d), tz=TIMEZONE)


def resample_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_chunk(client, start: date, end: date) -> pd.DataFrame | None:
    ts_start = to_ts(start)
    ts_end   = to_ts(end + timedelta(days=1))
    frames   = {}

    # 1. Carga real
    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        frames["actual_load_mw"] = resample_hourly(df["Actual Load"])
    except Exception as e:
        log.warning(f"    actual_load: {e}")
    time.sleep(PAUSE_SEC)

    # 2. Generacion real por tecnologia
    try:
        df_gen = client.query_generation(COUNTRY, start=ts_start, end=ts_end)
        for col, src_cols in GEN_MAPPING.items():
            values = None
            for src_col in src_cols:
                if src_col in df_gen.columns:
                    v = df_gen[src_col].fillna(0)
                    values = v if values is None else values + v
            if values is not None:
                frames[col] = resample_hourly(values)
    except Exception as e:
        log.warning(f"    generation: {e}")
    time.sleep(PAUSE_SEC)

    # 3. Flujos interconexion
    for (c_from, c_to, col) in [
        (COUNTRY, COUNTRY_FR, "flow_es_fr_mw"),
        (COUNTRY_FR, COUNTRY, "flow_fr_es_mw"),
        (COUNTRY, COUNTRY_PT, "flow_es_pt_mw"),
        (COUNTRY_PT, COUNTRY, "flow_pt_es_mw"),
    ]:
        try:
            df_flow = client.query_crossborder_flows(c_from, c_to, start=ts_start, end=ts_end)
            frames[col] = resample_hourly(df_flow)
        except Exception as e:
            log.warning(f"    flow {c_from}→{c_to}: {e}")
        time.sleep(PAUSE_SEC)

    if not frames:
        return None

    # Combinar en DataFrame
    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime_utc"
    df = df.reset_index()

    # datetime_local
    df["datetime_local"] = df["datetime_utc"].dt.tz_convert(TIMEZONE)

    # Columnas derivadas
    renew_cols = [c for c in ["solar_mw", "wind_mw", "hydro_mw", "biomass_mw",
                               "waste_mw", "pumping_generation_mw"] if c in df.columns]
    if renew_cols:
        df["renewable_generation_mw"] = df[renew_cols].fillna(0).sum(axis=1)

    thermal_cols = [c for c in ["ccgt_mw", "coal_mw", "cogeneration_mw",
                                 "other_generation_mw"] if c in df.columns]
    if thermal_cols:
        df["thermal_generation_mw"] = df[thermal_cols].fillna(0).sum(axis=1)

    if "actual_load_mw" in df.columns:
        ren = df.get("solar_mw", pd.Series(0, index=df.index)).fillna(0)
        win = df.get("wind_mw",  pd.Series(0, index=df.index)).fillna(0)
        df["residual_demand_mw"] = df["actual_load_mw"].fillna(0) - ren - win

    if "flow_es_fr_mw" in df.columns and "flow_fr_es_mw" in df.columns:
        df["net_flow_fr_mw"] = df["flow_fr_es_mw"].fillna(0) - df["flow_es_fr_mw"].fillna(0)

    if "flow_es_pt_mw" in df.columns and "flow_pt_es_mw" in df.columns:
        df["net_flow_pt_mw"] = df["flow_pt_es_mw"].fillna(0) - df["flow_es_pt_mw"].fillna(0)

    if "net_flow_fr_mw" in df.columns and "net_flow_pt_mw" in df.columns:
        df["net_flow_total_mw"] = df["net_flow_fr_mw"].fillna(0) + df["net_flow_pt_mw"].fillna(0)

    if "actual_load_mw" in df.columns and "net_flow_total_mw" in df.columns:
        df["net_load_mw"] = df["actual_load_mw"].fillna(0) - df["net_flow_total_mw"].fillna(0)

    return df

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_db_status(conn, start: date, end: date) -> tuple[set, set]:
    """Devuelve (horas_existentes, horas_con_nulls)."""
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt   = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    key_cols = ["actual_load_mw", "solar_mw", "wind_mw", "nuclear_mw"]
    null_check = " OR ".join([f"{c} IS NULL" for c in key_cols])

    with conn.cursor() as cur:
        cur.execute("SELECT datetime_utc FROM entsoe_data WHERE datetime_utc >= %s AND datetime_utc < %s",
                    (start_dt, end_dt))
        existing = {row[0] for row in cur.fetchall()}

        cur.execute(f"SELECT datetime_utc FROM entsoe_data WHERE datetime_utc >= %s AND datetime_utc < %s AND ({null_check})",
                    (start_dt, end_dt))
        with_nulls = {row[0] for row in cur.fetchall()}

    return existing, with_nulls


def build_expected_hours(start: date, end: date) -> set:
    hours   = set()
    current = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt  = datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc)
    while current <= end_dt:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def insert_rows(conn, df: pd.DataFrame, missing: set) -> int:
    df_f = df[df["datetime_utc"].isin(missing)]
    if df_f.empty:
        return 0
    cols = [c for c in ALL_COLS if c in df_f.columns]
    records = [
        tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
        for _, row in df_f.iterrows()
    ]
    sql = f"INSERT INTO entsoe_data ({', '.join(cols)}) VALUES %s ON CONFLICT (datetime_utc) DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, sql, records, page_size=500)
    conn.commit()
    return len(records)


def update_null_rows(conn, df: pd.DataFrame, with_nulls: set) -> int:
    if not with_nulls:
        return 0
    data_cols = [c for c in ALL_COLS if c not in ("datetime_utc", "datetime_local")]
    df_n = df[df["datetime_utc"].isin(with_nulls)]
    if df_n.empty:
        return 0

    updated  = 0
    cols_str = ", ".join([c for c in data_cols if c in df_n.columns])
    with conn.cursor() as cur:
        for _, row in df_n.iterrows():
            ts = row["datetime_utc"]
            cur.execute(f"SELECT {cols_str} FROM entsoe_data WHERE datetime_utc = %s", (ts,))
            db_row = cur.fetchone()
            if not db_row:
                continue
            cols_list = [c for c in data_cols if c in row.index]
            to_update = {col: row[col] for i, col in enumerate(cols_list)
                        if db_row[i] is None and not pd.isna(row.get(col))}
            if to_update:
                set_clause = ", ".join([f"{c} = %s" for c in to_update])
                cur.execute(f"UPDATE entsoe_data SET {set_clause}, updated_at = now() WHERE datetime_utc = %s",
                           list(to_update.values()) + [ts])
                updated += 1
    conn.commit()
    return updated

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    start_date, end_date = resolve_dates()

    import json
    from pathlib import Path
    _, db_config = load_config()
    creds  = json.load(open(Path(__file__).parent / "credentials.json"))
    client = EntsoePandasClient(api_key=creds["entsoe_token"])
    conn   = psycopg2.connect(**db_config)

    log.info("=" * 60)
    log.info(f"  MODE   : {MODE}")
    log.info(f"  Period : {start_date} → {end_date}")
    log.info("=" * 60)
    log.info("Connected to PostgreSQL OK")

    # PASO 1 — Estado BD
    log.info("Step 1 — Checking DB status...")
    existing, with_nulls = get_db_status(conn, start_date, end_date)
    expected = build_expected_hours(start_date, end_date)
    missing  = expected - existing

    log.info(f"  Expected hours  : {len(expected):,}")
    log.info(f"  Already in DB   : {len(existing):,}")
    log.info(f"  Missing hours   : {len(missing):,}")
    log.info(f"  Rows with nulls : {len(with_nulls):,}")

    hours_to_fetch = missing | with_nulls
    if not hours_to_fetch:
        log.info("  All data complete — nothing to do")
        conn.close()
        return

    # PASO 2 — Chunks
    total_ins, total_upd, errors = 0, 0, 0
    t0 = time.time()
    current = start_date
    chunk_n = 0

    while current <= end_date:
        chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end_date)
        chunk_n  += 1

        # Verificar si hay horas que cargar en este chunk
        chunk_hours = {h for h in hours_to_fetch
                      if datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
                      <= h <= datetime(chunk_end.year, chunk_end.month, chunk_end.day, 23, tzinfo=timezone.utc)}

        if not chunk_hours:
            log.info(f"  SKIP {current} → {chunk_end}")
            current = chunk_end + timedelta(days=1)
            continue

        log.info(f"  [{chunk_n}] Fetching {current} → {chunk_end} ({len(chunk_hours)} hours)")
        df = fetch_chunk(client, current, chunk_end)

        if df is not None and not df.empty:
            try:
                n_ins = insert_rows(conn, df, missing)
                n_upd = update_null_rows(conn, df, with_nulls)
                total_ins += n_ins
                total_upd += n_upd
                if n_ins > 0: log.info(f"    Inserted {n_ins} rows")
                if n_upd > 0: log.info(f"    Updated  {n_upd} rows")
            except Exception as e:
                log.error(f"    Error: {e}")
                conn.rollback()
                errors += 1
        else:
            log.warning(f"    No data for {current} → {chunk_end}")
            errors += 1

        current = chunk_end + timedelta(days=1)

    duration = time.time() - t0
    conn.close()
    log.info("=" * 60)
    log.info(f"  DONE: {total_ins} inserted | {total_upd} updated | {errors} errors | {duration:.1f}s")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
