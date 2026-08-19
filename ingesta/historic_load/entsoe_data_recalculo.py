"""
TFM Energia UCM — Recalculo de entsoe_gen_data + entsoe_load_inter

Adaptado 06/08/2026 a la division de entsoe_data en dos tablas.

MOTIVO DEL RECALCULO — el historico 2020 a julio-2026 quedo con dos
problemas al migrar, que NO se arreglan rellenando NULLs y exigen
sobrescribir:

  1. hydro_reservoir_mw arrastra el hydro_mw viejo, que era la SUMA de
     embalse (B12) + fluyente (B11). Hay que dejar solo B12.
  2. hydro_run_river_mw (B11) nace vacia.
  3. other_thermal_mw arrastra el other_generation_mw viejo, que mezclaba
     B20 (Other) con B15 (Other renewable) y se sumaba 100% a termica.
     Hay que dejar solo B20.
  4. other_renewable_mw (B15) nace vacia.

Las demas columnas migraron correctamente (solo cambiaron de nombre o de
tabla), pero se recalculan igual: la peticion a query_generation() trae
todas las tecnologias en una sola llamada, asi que verificarlas no cuesta
peticiones extra y confirma que la migracion fue limpia.

CORRECCIONES DE FONDO ya incorporadas (no revertir):
  - resample_hourly() SIEMPRE antes de cualquier filtro. Los datos nativos
    de ENTSO-E son cuarto-horarios desde octubre de 2025 (MTU15); filtrar
    primero deja 1 muestra por hora en vez del promedio de las 4
    (diferencia media 172 MW, maxima 633 MW en flow_fr_es_mw el 30-jul-2026).
  - Media y no suma: estos valores son POTENCIA (MW).
  - Las derivadas NO se calculan aqui: total_hydro_mw, total_renew_mw,
    total_thermal_mw, net_load_mw, net_flow_*_mw y total_net_flow_mw son
    GENERATED ALWAYS AS STORED en PostgreSQL.
  - pumping_gen_mw y battery_gen_mw quedan FUERA de total_renew_mw
    (criterio REE enero 2025: la entrega de energia almacenada dejo de
    considerarse generacion).

Uso: ajustar START_DATE / END_DATE abajo y ejecutar. Conviene lanzarlo con
nohup: son ~344 bloques y algo menos de una hora.
"""

import sys
import json
import time
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from entsoe import EntsoePandasClient

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ══════════════════════════════════════════════════════════════════
START_DATE = "2020-01-01"
END_DATE   = "2026-08-06"
CHUNK_DAYS = 7
TOLERANCIA = 0.01   # diferencias menores a esto se ignoran (redondeo)
CREAR_FILAS_NUEVAS = False   # True = inserta horas que no existan en BD
# ══════════════════════════════════════════════════════════════════

COUNTRY    = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"
TIMEZONE   = "Europe/Madrid"
PAUSE_SEC  = 1.0

TABLA_GEN  = "entsoe_gen_data"
TABLA_LOAD = "entsoe_load_inter"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("recalculo_entsoe")

# ── Mapeo de tecnologias (16 columnas, 23 claves PSR) ──────────────────────────
# De los 21 codigos PSR del catalogo ENTSO-E la zona ES solo reporta 13.
# Los ocho que nunca aparecen se plegan en columnas que si tienen dato, para
# capturarlos si algun dia se publican sin tener que tocar el esquema.

GEN_MAPPING = {
    # No despachables
    "solar_mw":            [("Solar", "Actual Aggregated")],                       # B16 (FV + termosolar)
    "wind_mw":             [("Wind Onshore", "Actual Aggregated"),                 # B19
                            ("Wind Offshore", "Actual Aggregated")],              # B18 (no existe en ES)
    "hydro_run_river_mw":  [("Hydro Run-of-river and poundage", "Actual Aggregated")],  # B11

    # Hidraulica despachable
    "hydro_reservoir_mw":  [("Hydro Water Reservoir", "Actual Aggregated")],       # B12

    # Almacenamiento (fuera de total_renew_mw)
    "pumping_gen_mw":      [("Hydro Pumped Storage", "Actual Aggregated")],        # B10 gen
    "pumping_cons_mw":     [("Hydro Pumped Storage", "Actual Consumption")],       # B10 cons
    "battery_gen_mw":      [("Energy storage", "Actual Aggregated")],              # B25 gen
    "battery_cons_mw":     [("Energy storage", "Actual Consumption")],             # B25 cons

    # Otras renovables
    "biomass_mw":          [("Biomass", "Actual Aggregated")],                     # B01
    "waste_mw":            [("Waste", "Actual Aggregated")],                       # B17
    "other_renewable_mw":  [("Other renewable", "Actual Aggregated"),              # B15
                            ("Geothermal", "Actual Aggregated"),                   # B09 (no existe en ES)
                            ("Marine", "Actual Aggregated")],                      # B13 (no existe en ES)

    # Base
    "nuclear_mw":          [("Nuclear", "Actual Aggregated")],                     # B14

    # Fosiles
    "gas_mw":              [("Fossil Gas", "Actual Aggregated")],                  # B04 (incluye cogeneracion)
    "coal_mw":             [("Fossil Hard coal", "Actual Aggregated"),             # B05 (cerrado desde 2021)
                            ("Fossil Brown coal/Lignite", "Actual Aggregated")],  # B02 (nunca reportado en ES)
    "oil_mw":              [("Fossil Oil", "Actual Aggregated")],                  # B06 (residual ~30 MW)
    "other_thermal_mw":    [("Other", "Actual Aggregated"),                        # B20
                            ("Fossil Coal-derived gas", "Actual Aggregated"),      # B03 (no reportado en ES)
                            ("Fossil Oil shale", "Actual Aggregated"),             # B07 (no existe en ES)
                            ("Fossil Peat", "Actual Aggregated")],                 # B08 (no existe en ES)
}

# Columnas base escribibles de cada tabla (las GENERATED las calcula PostgreSQL)
GEN_COLS = [
    "solar_mw", "wind_mw",
    "hydro_run_river_mw", "hydro_reservoir_mw",
    "pumping_gen_mw", "pumping_cons_mw",
    "battery_gen_mw", "battery_cons_mw",
    "biomass_mw", "waste_mw", "other_renewable_mw",
    "nuclear_mw",
    "gas_mw", "coal_mw", "oil_mw", "other_thermal_mw",
]

LOAD_COLS = [
    "actual_load_mw",
    "flow_es_fr_mw", "flow_fr_es_mw",
    "flow_es_pt_mw", "flow_pt_es_mw",
    "ntc_imp_fr_mw", "ntc_exp_fr_mw",
    "ntc_imp_pt_mw", "ntc_exp_pt_mw",
]

# Las columnas de Marruecos se ELIMINARON de entsoe_load_inter el 17/08/2026:
# Marruecos no es miembro de ENTSO-E y esas cuatro columnas estaban vacias al
# 100% en 58.054 filas. El dato esta en ESIOS (10209 saldo, 1846/1850 NTC).
# Las NTC de FR y PT si se descargan aqui como respaldo, aunque la fuente
# primaria son los indicadores ESIOS 488-494, con historico desde 2020.

# ── Descarga ───────────────────────────────────────────────────────────────────

def to_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(str(d), tz=TIMEZONE)


def resample_hourly(series: pd.Series) -> pd.Series:
    """Media horaria. Nunca filtrar antes de llamar a esto (ver docstring)."""
    if series.empty:
        return series
    return series.resample("h").mean()


def fetch_chunk(client, start: date, end: date) -> pd.DataFrame | None:
    ts_start = to_ts(start)
    ts_end   = to_ts(end + timedelta(days=1))
    frames   = {}

    # ── Carga real ──
    try:
        df = client.query_load(COUNTRY, start=ts_start, end=ts_end)
        frames["actual_load_mw"] = resample_hourly(df["Actual Load"])
    except Exception as e:
        log.warning(f"    actual_load: {str(e).split('securityToken')[0]}")
    time.sleep(PAUSE_SEC)

    # ── Generacion por tecnologia ──
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

        # Guardarrail: tecnologias que ENTSO-E devuelve y no estan mapeadas.
        # Sin esto se descartarian en silencio (es el fallo que hizo perder
        # 10 de las 21 tecnologias del catalogo sin enterarse).
        mapeadas = {src for lista in GEN_MAPPING.values() for src in lista}
        huerfanas = [c for c in df_gen.columns if c not in mapeadas]
        if huerfanas:
            log.warning(f"    [SIN MAPEAR] {huerfanas}")

    except Exception as e:
        log.warning(f"    generation: {str(e).split('securityToken')[0]}")
    time.sleep(PAUSE_SEC)

    # ── Flujos de interconexion ──
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
            # No incluir la URL: lleva el securityToken en texto plano
            log.warning(f"    flow {c_from}→{c_to}: {str(e).split('securityToken')[0]}")
        time.sleep(PAUSE_SEC)


    # ── NTC de interconexion (backup ENTSO-E, fuente primaria es ESIOS) ──
    for (c_from, c_to, col) in [
        (COUNTRY_FR, COUNTRY, "ntc_imp_fr_mw"),
        (COUNTRY, COUNTRY_FR, "ntc_exp_fr_mw"),
        (COUNTRY_PT, COUNTRY, "ntc_imp_pt_mw"),
        (COUNTRY, COUNTRY_PT, "ntc_exp_pt_mw"),
    ]:
        try:
            df_ntc = client.query_net_transfer_capacity_dayahead(
                c_from, c_to, start=ts_start, end=ts_end)
            frames[col] = resample_hourly(df_ntc)
        except Exception as e:
            log.warning(f"    NTC {c_from}→{c_to}: {str(e).split('securityToken')[0]}")
        time.sleep(PAUSE_SEC)

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime"
    return df.reset_index()

# ── Escritura ──────────────────────────────────────────────────────────────────

def recalcular_tabla(conn, df_nuevo: pd.DataFrame, tabla: str, cols: list,
                     contador: dict) -> int:
    """
    Compara cada celda contra la BD y sobrescribe solo si difiere mas alla de
    la tolerancia (no solo si es NULL).

    En lote: 1 SELECT + 1 UPDATE por columna y bloque, en vez de 2 consultas
    por fila. Con ~70 ms de latencia al servidor OVH, la version por filas
    tardaba unas 10 horas para 2020-2026; asi baja a algo menos de una.
    """
    presentes = [c for c in cols if c in df_nuevo.columns]
    if not presentes or df_nuevo.empty:
        return 0

    ts_list = [r["datetime"] for _, r in df_nuevo.iterrows()]
    cols_str = ", ".join(presentes)
    corregidas = 0

    with conn.cursor() as cur:
        # Estado actual de todo el bloque en una sola consulta
        cur.execute(
            f"SELECT datetime, {cols_str} FROM {tabla} WHERE datetime = ANY(%s)",
            (ts_list,)
        )
        actuales = {row[0]: row[1:] for row in cur.fetchall()}

        # Un UPDATE en lote por columna
        for i, col in enumerate(presentes):
            cambios = []
            for _, row in df_nuevo.iterrows():
                ts = row["datetime"]
                nuevo = row.get(col)
                if pd.isna(nuevo):
                    continue
                if ts not in actuales:
                    continue          # esa hora no existe en BD
                viejo = actuales[ts][i]
                nuevo = round(float(nuevo), 2)
                if viejo is None or abs(float(viejo) - nuevo) > TOLERANCIA:
                    cambios.append((ts, nuevo))

            if not cambios:
                continue

            execute_values(cur, f"""
                UPDATE {tabla} AS t
                SET {col} = v.valor, updated_at = now()
                FROM (VALUES %s) AS v(ts, valor)
                WHERE t.datetime = v.ts
            """, cambios, template="(%s, %s::numeric)", page_size=500)

            contador[f"{tabla}.{col}"] = contador.get(f"{tabla}.{col}", 0) + len(cambios)
            corregidas += len(cambios)

    conn.commit()
    return corregidas


def insertar_faltantes(conn, df_nuevo: pd.DataFrame, tabla: str, cols: list,
                       contador: dict) -> int:
    """Inserta horas que no existan en BD. Solo si CREAR_FILAS_NUEVAS."""
    presentes = [c for c in cols if c in df_nuevo.columns]
    if not presentes or df_nuevo.empty:
        return 0

    ts_list = [r["datetime"] for _, r in df_nuevo.iterrows()]
    with conn.cursor() as cur:
        cur.execute(f"SELECT datetime FROM {tabla} WHERE datetime = ANY(%s)",
                    (ts_list,))
        existentes = {row[0] for row in cur.fetchall()}

    df_new = df_nuevo[~df_nuevo["datetime"].isin(existentes)]
    if df_new.empty:
        return 0

    insert_cols = ["datetime"] + presentes
    records = [tuple(None if pd.isna(row.get(c)) else row.get(c) for c in insert_cols)
               for _, row in df_new.iterrows()]
    with conn.cursor() as cur:
        execute_values(cur,
            f"INSERT INTO {tabla} ({', '.join(insert_cols)}) VALUES %s "
            f"ON CONFLICT (datetime) DO NOTHING",
            records, page_size=500)
    conn.commit()

    contador[f"{tabla}.__INSERT__"] = contador.get(f"{tabla}.__INSERT__", 0) + len(records)
    return len(records)

# ── Main ───────────────────────────────────────────────────────────────────────

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
    end   = date.fromisoformat(END_DATE)

    _, db_config = load_config()
    creds  = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    client = EntsoePandasClient(api_key=creds["entsoe_token"])
    conn   = psycopg2.connect(**db_config)

    bloques = rango_en_bloques(start, end, CHUNK_DAYS)

    log.info("=" * 62)
    log.info(f"Recalculo {TABLA_GEN} + {TABLA_LOAD}")
    log.info(f"Periodo      : {start} a {end}")
    log.info(f"Bloques      : {len(bloques)} de {CHUNK_DAYS} dias")
    log.info(f"Columnas     : gen={len(GEN_COLS)} load={len(LOAD_COLS)}")
    log.info(f"Tolerancia   : {TOLERANCIA}")
    log.info(f"Crear filas  : {CREAR_FILAS_NUEVAS}")
    log.info("=" * 62)

    contador = {}
    fallidos = []

    for i, (b_start, b_end) in enumerate(bloques, 1):
        t0 = time.time()
        df = fetch_chunk(client, b_start, b_end)
        if df is None or df.empty:
            log.warning(f"[{i}/{len(bloques)}] {b_start} a {b_end}: sin datos, se omite")
            fallidos.append((b_start, b_end))
            continue

        if CREAR_FILAS_NUEVAS:
            insertar_faltantes(conn, df, TABLA_GEN,  GEN_COLS,  contador)
            insertar_faltantes(conn, df, TABLA_LOAD, LOAD_COLS, contador)

        n_gen  = recalcular_tabla(conn, df, TABLA_GEN,  GEN_COLS,  contador)
        n_load = recalcular_tabla(conn, df, TABLA_LOAD, LOAD_COLS, contador)

        log.info(f"[{i}/{len(bloques)}] {b_start} a {b_end}: "
                 f"gen={n_gen} load={n_load} corregidas ({time.time()-t0:.1f}s)")

    conn.close()

    log.info("\n" + "=" * 62)
    log.info("RESUMEN DE CELDAS CORREGIDAS POR COLUMNA")
    log.info("=" * 62)
    if not contador:
        log.info("  Ninguna celda necesito correccion.")
    else:
        for col, n in sorted(contador.items(), key=lambda x: -x[1]):
            log.info(f"  {col:40s}: {n:>8} celdas")
        log.info("-" * 62)
        log.info(f"  {'TOTAL':40s}: {sum(contador.values()):>8} celdas")

    if fallidos:
        log.warning(f"\n{len(fallidos)} bloques sin datos (relanzar solo estos):")
        for b_start, b_end in fallidos:
            log.warning(f"  {b_start} a {b_end}")
    log.info("=" * 62)


if __name__ == "__main__":
    main()
