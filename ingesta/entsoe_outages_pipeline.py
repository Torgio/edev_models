"""
entsoe_outages_pipeline.py — indisponibilidades de generacion, ENTSO-E
======================================================================

QUE RESUELVE
La potencia disponible de ESIOS (indicadores 472-478, tabla
esios_capacity_available) tiene horizonte D+1 pero SIN perfil horario:
verificado el 21-ago-2026 que las 24 horas del D+1 son identicas — recorrido
0,0 MW en las seis tecnologias — mientras que en D-2 el ciclo combinado se
mueve 838 MW y la hidraulica 193. La resolucion fina solo aparece en las
revisiones posteriores al dia de entrega, luego no es usable sin fuga.

ENTSO-E publica lo contrario y mejor: indisponibilidades unidad por unidad,
con ventana de inicio y fin, potencia nominal, tipo de produccion en la misma
taxonomia PSR que usa generation, y —lo decisivo— created_doc_time, la marca
de cuando se publico el aviso. Eso permite construir una serie horaria de
capacidad no disponible y DEMOSTRAR que no hay fuga, registro a registro, en
vez de suponerlo.

DOS TABLAS
  entsoe_outages          registros crudos, uno por aviso. Es la fuente de
                          verdad: guarda created_doc_time, docstatus y
                          revision, asi que la agregacion se puede recalcular
                          con cualquier criterio de corte sin volver a la API.

  capacity_unavailable    agregado horario por tecnologia, ya filtrado. Es la
                          que entra al dataset.

TRES FILTROS QUE NO SON OPCIONALES
  1. docstatus = 'Cancelled'. Verificado el 21-ago-2026: dos registros de
     CORTES 1 y 2 aparecian cancelados. Sumarlos restaria capacidad que si
     estaba disponible.
  2. revision. El mismo mrid se republica corregido, hasta revision 4 en los
     datos probados. Solo vale la ultima por unidad y ventana.
  3. created_doc_time <= corte. El corte por defecto son las 12:00 del dia
     D-1, cierre de ofertas del mercado diario. Un aviso publicado despues no
     se conocia al ofertar, asi que incluirlo seria fuga. Los registros
     probados se publicaron con meses de antelacion (octubre-2025 para
     agosto-2026), pero las averias no planificadas aparecen de golpe y son
     45 de los 56 registros probados.

MAGNITUD INDISPONIBLE = nominal_power - avail_qty
Con avail_qty = 0 la unidad esta fuera por completo. ENTSO-E tambien publica
indisponibilidades PARCIALES, con avail_qty > 0, donde solo falta una parte de
la potencia: por eso se resta en vez de usar nominal_power directamente.

USO
    python entsoe_outages_pipeline.py                     # D+1 a D+7
    python entsoe_outages_pipeline.py --dias 30
    python entsoe_outages_pipeline.py --desde 2026-01-01 --hasta 2026-06-30
    python entsoe_outages_pipeline.py --sin-corte         # ignora la fuga
    python entsoe_outages_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent))
from config import load_config

try:
    from entsoe import EntsoePandasClient
    from entsoe.exceptions import NoMatchingDataError
except ImportError:
    raise SystemExit("Falta entsoe-py:  pip install entsoe-py")

TZ = "Europe/Madrid"
PAIS = "ES"
TABLA_RAW = "entsoe_outages"
TABLA_AGG = "capacity_unavailable"

HORIZONTE_DIAS = 7
PAUSA_API = 1.0
CREDS = Path(__file__).parent / "credentials.json"

# plant_type de ENTSO-E -> columna del agregado. Se respeta el desglose de
# generation: embalse y bombeo van juntos porque alli forman c_ghydrodispatch,
# y el fluyente aparte porque no es despachable.
MAPA = {
    "Hydro Pumped Storage":   "unav_hydrodispatch_mw",
    "Hydro Water Reservoir":  "unav_hydrodispatch_mw",
    "Hydro Run-of-river and poundage": "unav_hydroriver_mw",
    "Fossil Gas":             "unav_gas_mw",
    "Fossil Hard coal":       "unav_coal_mw",
    "Fossil Oil":             "unav_oil_mw",
    "Nuclear":                "unav_nuclear_mw",
    "Solar":                  "unav_solar_mw",
    "Wind Onshore":           "unav_wind_mw",
}
COLS_AGG = sorted(set(MAPA.values())) + ["unav_other_mw"]


DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLA_RAW} (
    mrid              text,
    revision          integer,
    created_doc_time  timestamptz NOT NULL,
    start_ts          timestamptz NOT NULL,
    end_ts            timestamptz NOT NULL,
    plant_type        text,
    businesstype      text,
    docstatus         text,
    nominal_power     numeric,
    avail_qty         numeric,
    unit_name         text,
    resource_name     text,
    location          text,
    updated_at        timestamptz DEFAULT now(),
    PRIMARY KEY (mrid, revision)
);
COMMENT ON TABLE {TABLA_RAW} IS
'Avisos de indisponibilidad de unidades de generacion (ENTSO-E art. 15.1.A/B).
Registros crudos, sin filtrar: incluye cancelados y todas las revisiones. Es la
fuente de verdad, asi que capacity_unavailable se puede recalcular con
cualquier criterio de corte sin volver a la API. created_doc_time es la marca
de publicacion del aviso y es lo que permite demostrar la ausencia de fuga.';

CREATE INDEX IF NOT EXISTS {TABLA_RAW}_ventana_idx
    ON {TABLA_RAW} (start_ts, end_ts);
CREATE INDEX IF NOT EXISTS {TABLA_RAW}_creado_idx
    ON {TABLA_RAW} (created_doc_time);

CREATE TABLE IF NOT EXISTS {TABLA_AGG} (
    datetime    timestamptz PRIMARY KEY,
    {', '.join(f'{c} numeric' for c in COLS_AGG)},
    unav_total_mw numeric GENERATED ALWAYS AS (
        {' + '.join(f'COALESCE({c},0)' for c in COLS_AGG)}) STORED,
    updated_at  timestamptz DEFAULT now()
);
COMMENT ON TABLE {TABLA_AGG} IS
'Capacidad NO disponible por tecnologia y hora, agregada desde entsoe_outages.
Filtrado: sin cancelados, ultima revision por unidad, y solo avisos publicados
antes de las 12:00 del dia D-1 (cierre del mercado diario). Sustituye el uso de
esios_capacity_available como feature: aquella tiene horizonte D+1 pero sin
perfil horario (recorrido 0,0 MW en las 24 horas), y su variacion intradiaria
es retrospectiva.';
"""


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar(client, ini: date, fin: date) -> pd.DataFrame:
    """Avisos que solapan con la ventana. ENTSO-E devuelve el aviso completo,
    no recortado, asi que un mantenimiento largo aparece entero."""
    t0 = pd.Timestamp(ini, tz=TZ)
    t1 = pd.Timestamp(fin + timedelta(days=1), tz=TZ)
    try:
        time.sleep(PAUSA_API)
        df = client.query_unavailability_of_generation_units(
            PAIS, start=t0, end=t1)
    except NoMatchingDataError:
        print("    sin avisos en la ventana")
        return pd.DataFrame()
    except Exception as e:
        print(f"    ERROR {type(e).__name__}: {str(e)[:120]}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index().rename(columns={
        "created_doc_time": "created_doc_time",
        "start": "start_ts", "end": "end_ts",
        "production_resource_psr_name": "unit_name",
        "production_resource_name": "resource_name",
        "production_resource_location": "location",
    })
    for c in ("nominal_power", "avail_qty", "revision"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def escribir_raw(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["mrid", "revision", "created_doc_time", "start_ts", "end_ts",
            "plant_type", "businesstype", "docstatus", "nominal_power",
            "avail_qty", "unit_name", "resource_name", "location"]
    for c in cols:
        if c not in df.columns:
            df[c] = None

    df = df.drop_duplicates(subset=["mrid", "revision"], keep="last")

    filas = [tuple(None if pd.isna(v) else v for v in fila)
             for fila in df[cols].to_numpy()]

    sql = f"""
        INSERT INTO {TABLA_RAW} ({', '.join(cols)}) VALUES %s
        ON CONFLICT (mrid, revision) DO UPDATE SET
        {', '.join(f'{c} = EXCLUDED.{c}' for c in cols[2:])},
        updated_at = now()
    """
    with conn, conn.cursor() as cur:
        execute_values(cur, sql, filas, page_size=200)
    return len(filas)

# ── Agregacion ────────────────────────────────────────────────────────────────

SQL_AGG = f"""
WITH ultima AS (
    -- Solo la ultima revision de cada aviso, y sin cancelados.
    SELECT DISTINCT ON (mrid) *
    FROM {TABLA_RAW}
    WHERE COALESCE(docstatus, '') <> 'Cancelled'
    ORDER BY mrid, revision DESC
),
vigente AS (
    -- CORTE ANTIFUGA: para una hora del dia D solo cuentan los avisos
    -- publicados antes de las 12:00 del dia D-1, cierre de ofertas.
    SELECT u.*,
           u.nominal_power - COALESCE(u.avail_qty, 0) AS mw_fuera
    FROM ultima u
    WHERE u.nominal_power IS NOT NULL
),
horas AS (
    SELECT generate_series(
             date_trunc('hour', v.start_ts),
             v.end_ts - interval '1 hour',
             interval '1 hour') AS datetime,
           v.plant_type, v.mw_fuera, v.created_doc_time
    FROM vigente v
    WHERE v.end_ts > v.start_ts
)
SELECT datetime,
       {', '.join(
           f"""sum(mw_fuera) FILTER (WHERE {
               ' OR '.join(f"plant_type = '{k}'" for k, vv in MAPA.items() if vv == c)
           }) AS {c}""" for c in COLS_AGG if c != 'unav_other_mw')},
       sum(mw_fuera) FILTER (
           WHERE plant_type IS NULL
              OR plant_type NOT IN ({', '.join(f"'{k}'" for k in MAPA)})
       ) AS unav_other_mw
FROM horas
WHERE datetime >= %(ini)s AND datetime < %(fin)s
  AND (%(sin_corte)s OR created_doc_time
       < date_trunc('day', datetime) - interval '12 hours')
GROUP BY datetime
ORDER BY datetime
"""


def agregar(conn, ini: date, fin: date, sin_corte: bool) -> int:
    params = {"ini": pd.Timestamp(ini, tz=TZ),
              "fin": pd.Timestamp(fin + timedelta(days=1), tz=TZ),
              "sin_corte": sin_corte}
    with conn.cursor() as cur:
        cur.execute(SQL_AGG, params)
        filas = cur.fetchall()
    if not filas:
        return 0

    cols = ["datetime"] + COLS_AGG
    sql = f"""
        INSERT INTO {TABLA_AGG} ({', '.join(cols)}) VALUES %s
        ON CONFLICT (datetime) DO UPDATE SET
        {', '.join(f'{c} = EXCLUDED.{c}' for c in COLS_AGG)},
        updated_at = now()
    """
    with conn, conn.cursor() as cur:
        execute_values(cur, sql, filas, page_size=500)
    return len(filas)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    hoy = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--dias", type=int, default=HORIZONTE_DIAS)
    p.add_argument("--desde", type=date.fromisoformat)
    p.add_argument("--hasta", type=date.fromisoformat)
    p.add_argument("--sin-corte", action="store_true",
                   help="No aplica el filtro antifuga de las 12:00 de D-1")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    ini = args.desde or hoy
    fin = args.hasta or (hoy + timedelta(days=args.dias))

    print("=" * 72)
    print(f"{TABLA_RAW} + {TABLA_AGG} — {ini} a {fin}"
          + ("   [DRY RUN]" if args.dry_run else "")
          + ("   [SIN CORTE ANTIFUGA]" if args.sin_corte else ""))
    print("=" * 72)

    creds = json.loads(CREDS.read_text(encoding="utf-8"))
    client = EntsoePandasClient(api_key=creds["entsoe_token"])
    _, db_config = load_config()

    print(f"\nDescargando avisos...")
    df = descargar(client, ini, fin)
    print(f"  {len(df)} avisos")
    if len(df):
        print(f"  cancelados: {(df.get('docstatus') == 'Cancelled').sum()}")
        print(f"  no planificados: "
              f"{(df.get('businesstype') == 'Unplanned outage').sum()}")
        print(f"  tecnologias: {df['plant_type'].nunique()}")
        desconocidas = set(df["plant_type"].dropna()) - set(MAPA)
        if desconocidas:
            print(f"  AVISO, plant_type sin mapear -> unav_other_mw: "
                  f"{sorted(desconocidas)}")

    if args.dry_run:
        if len(df):
            print("\n" + df.head(10).to_string())
        return

    conn = psycopg2.connect(**db_config)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)

    n_raw = escribir_raw(conn, df)
    print(f"\n{TABLA_RAW}: {n_raw} avisos escritos")

    n_agg = agregar(conn, ini, fin, args.sin_corte)
    print(f"{TABLA_AGG}: {n_agg} horas agregadas")

    with conn.cursor() as cur:
        cur.execute(f"SELECT min(datetime), max(datetime), "
                    f"round(avg(unav_total_mw)) FROM {TABLA_AGG}")
        lo, hi, media = cur.fetchone()
    print(f"  rango {lo} .. {hi} | media {media} MW no disponibles")
    conn.close()


if __name__ == "__main__":
    main()
