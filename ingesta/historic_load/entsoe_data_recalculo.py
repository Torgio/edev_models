"""
TFM Energia UCM — Recalculo de entsoe_data (correccion bug filtro/resample)
El pipeline diario (entsoe_daily_pipeline.py) tenia un bug de orden de
operaciones: filtraba a horas exactas ANTES de promediar los cuartos de
hora nativos de ENTSO-E, dejando solo 1 muestra de 15 min por hora en vez
del promedio real de las 4 (confirmado: diferencia media 172.74 MW, maxima
633.32 MW el 30-jul-2026 en flow_fr_es_mw). Este script de carga historica
(entsoe_data_history.py) SIEMPRE hizo bien la agregacion (nunca filtra antes
de resamplear), asi que reutilizamos su misma logica de descarga para volver
a calcular el rango afectado y sobreescribir SOLO las celdas que difieran
del valor ya guardado (mas alla de una tolerancia de redondeo).

Uso: ajustar START_DATE / END_DATE abajo y ejecutar.
"""

import sys
import json
import time
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from entsoe import EntsoePandasClient

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ══════════════════════════════════════════════════════════════════
START_DATE = "2025-06-24"
END_DATE   = "2025-07-01"
CHUNK_DAYS = 7
TOLERANCIA = 0.01   # diferencias menores a esto se ignoran (redondeo)
# ══════════════════════════════════════════════════════════════════

COUNTRY    = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"
TIMEZONE   = "Europe/Madrid"
PAUSE_SEC  = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("recalculo_entsoe")

GEN_MAPPING = {
    "solar_mw":                  [("Solar", "Actual Aggregated")],
    "wind_mw":                   [("Wind Onshore", "Actual Aggregated")],
    "nuclear_mw":                [("Nuclear", "Actual Aggregated")],
    "ccgt_mw":                   [("Fossil Gas", "Actual Aggregated")],
    "coal_mw":                   [("Fossil Hard coal", "Actual Aggregated")],
    "biomass_mw":                [("Biomass", "Actual Aggregated")],
    "waste_mw":                  [("Waste", "Actual Aggregated")],
    "other_generation_mw":       [("Other", "Actual Aggregated"),
                                   ("Other renewable", "Actual Aggregated")],
    "hydro_mw":                  [("Hydro Water Reservoir", "Actual Aggregated"),
                                   ("Hydro Run-of-river and poundage", "Actual Aggregated")],
    "pumping_generation_mw":     [("Hydro Pumped Storage", "Actual Aggregated")],
    "pumping_consumption_mw":    [("Hydro Pumped Storage", "Actual Consumption")],
    "battery_storage_gen_mw":    [("Energy storage", "Actual Aggregated")],
    "battery_storage_cons_mw":   [("Energy storage", "Actual Consumption")],
    "cogeneration_mw":           [("Fossil Oil", "Actual Aggregated")],
}

DATA_COLS = [
    "actual_load_mw",
    "solar_mw", "wind_mw", "nuclear_mw", "ccgt_mw", "coal_mw",
    "biomass_mw", "waste_mw", "hydro_mw", "cogeneration_mw",
    "other_generation_mw", "pumping_generation_mw", "pumping_consumption_mw",
    "battery_storage_gen_mw", "battery_storage_cons_mw",
    "renewable_generation_mw", "thermal_generation_mw",
    "residual_demand_mw", "net_load_mw",
    "flow_es_fr_mw", "flow_fr_es_mw", "net_flow_fr_mw",
    "flow_es_pt_mw", "flow_pt_es_mw", "net_flow_pt_mw",
    "net_flow_total_mw",
]


def to_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(str(d), tz=TIMEZONE)


def resample_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_chunk(client, start: date, end: date) -> pd.DataFrame | None:
    """Misma logica que entsoe_data_history.py (ya correcta: nunca filtra
    antes de resamplear)."""
    ts_start = to_ts(start)
    ts_end   = to_ts(end + timedelta(days=1))
    frames   = {}

    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        frames["actual_load_mw"] = resample_hourly(df["Actual Load"])
    except Exception as e:
        log.warning(f"    actual_load: {e}")
    time.sleep(PAUSE_SEC)

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

    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime_utc"
    df = df.reset_index()

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


def recalcular_bloque(conn, df_nuevo: pd.DataFrame, contador: dict):
    """Compara cada celda nueva contra la BD y actualiza solo si difiere
    mas alla de la tolerancia (no solo si es NULL)."""
    cols_presentes = [c for c in DATA_COLS if c in df_nuevo.columns]
    cols_str = ", ".join(cols_presentes)

    with conn.cursor() as cur:
        for _, row in df_nuevo.iterrows():
            ts = row["datetime_utc"]
            cur.execute(f"SELECT {cols_str} FROM entsoe_data WHERE datetime_utc = %s", (ts,))
            db_row = cur.fetchone()
            if not db_row:
                continue  # esa hora no existe en BD, no la creamos aqui

            to_update = {}
            for i, col in enumerate(cols_presentes):
                nuevo_val = row.get(col)
                if pd.isna(nuevo_val):
                    continue
                viejo_val = db_row[i]
                nuevo_val = round(float(nuevo_val), 2)

                if viejo_val is None or abs(float(viejo_val) - nuevo_val) > TOLERANCIA:
                    to_update[col] = nuevo_val
                    contador[col] = contador.get(col, 0) + 1

            if to_update:
                set_clause = ", ".join([f"{c} = %s" for c in to_update])
                cur.execute(
                    f"UPDATE entsoe_data SET {set_clause}, updated_at = now() WHERE datetime_utc = %s",
                    list(to_update.values()) + [ts]
                )
    conn.commit()


def rango_en_bloques(start: date, end: date, chunk_days: int):
    bloques = []
    actual = start
    while actual <= end:
        fin_bloque = min(actual + timedelta(days=chunk_days - 1), end)
        bloques.append((actual, fin_bloque))
        actual = fin_bloque + timedelta(days=1)
    return bloques


def main():
    start = date.fromisoformat(START_DATE)
    end = date.fromisoformat(END_DATE)

    log.info(f"Recalculo entsoe_data: {start} a {end}")

    _, db_config = load_config()
    creds = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    client = EntsoePandasClient(api_key=creds["entsoe_token"])
    conn = psycopg2.connect(**db_config)

    bloques = rango_en_bloques(start, end, CHUNK_DAYS)
    log.info(f"Total bloques de {CHUNK_DAYS} dias: {len(bloques)}\n")

    contador_global = {}

    for i, (b_start, b_end) in enumerate(bloques, 1):
        log.info(f"[{i}/{len(bloques)}] Bloque {b_start} a {b_end}...")
        df = fetch_chunk(client, b_start, b_end)
        if df is None or df.empty:
            log.warning(f"  Sin datos para este bloque, se omite")
            continue
        recalcular_bloque(conn, df, contador_global)

    conn.close()

    log.info("\n" + "=" * 60)
    log.info("RESUMEN DE CELDAS CORREGIDAS POR COLUMNA")
    log.info("=" * 60)
    if not contador_global:
        log.info("Ninguna celda necesito correccion.")
    else:
        for col, n in sorted(contador_global.items(), key=lambda x: -x[1]):
            log.info(f"  {col:28s}: {n} celdas corregidas")
    log.info(f"\nTOTAL celdas corregidas: {sum(contador_global.values())}")


if __name__ == "__main__":
    main()