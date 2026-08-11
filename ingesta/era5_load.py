"""
TFM Energia UCM — ERA5 Weather Historical Loader
Descarga datos de reanalisis ERA5 (Copernicus CDS) y los carga en:
  - tabla era5_weather_agg (PostgreSQL) — medias espaciales horarias, para EDA/features
  - tensor .npy por mes                 — para el CNN de F12

Cobertura espacial: España peninsular + Baleares (excluye Canarias, no
interconectada al sistema peninsular). Mismo bounding box que
ecmwf_forecast_load.py, para que ambos tensores sean compatibles.

IMPORTANTE:
  ERA5 es un REANALISIS historico (retraso de ~5 dias en su version
  preliminar ERA5T, ~2-3 meses en la version final). Este script es SOLO
  para historico/entrenamiento. El pipeline diario de produccion (prediccion
  D+1) necesita una fuente de PRONOSTICO distinta -> ver ecmwf_forecast_load.py.

Fuente: Copernicus Climate Data Store (CDS), dataset reanalysis-era5-single-levels
Autenticacion CDS: Personal Access Token leido desde credentials.json
(campo "cds_api_key"), via config.load_cds_key().

Logica anti-duplicados (mismo criterio que commodities_load.py):
  - Consulta BD antes de descargar (a nivel de mes, para no gastar cuota CDS
    en meses ya completos)
  - INSERT solo timestamps nuevos
  - UPDATE solo columnas NULL en timestamps existentes (recarga parcial previa)
  - ON CONFLICT DO NOTHING como doble proteccion

Requisitos:
  pip install cdsapi xarray netCDF4 psycopg2-binary numpy --break-system-packages

Usage:
    python era5_historical_load.py                          # 2020-01 -> mes actual - 1
    python era5_historical_load.py --start 2024-01 --end 2024-12
    python era5_historical_load.py --start 2026-05 --end 2026-06 --force
"""

import argparse
import logging
import sys
import tempfile
import time
import zipfile
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

try:
    import cdsapi
    import xarray as xr
except ImportError:
    print("Faltan dependencias: pip install cdsapi xarray netCDF4 --break-system-packages")
    sys.exit(1)

from config import load_config, load_cds_key

CDS_URL = "https://cds.climate.copernicus.eu/api"
TENSOR_OUTPUT_DIR = Path("/data/era5_tensors")

# ── Configuracion ──────────────────────────────────────────────────────────────

# España peninsular + Baleares. MISMO bounding box que ecmwf_forecast_load.py
# (necesario para que el tensor de pronostico sea compatible con el de
# entrenamiento). Excluye Canarias: sistema electrico no interconectado a la
# peninsula, no aporta al precio del mercado peninsular (objetivo del TFM).
AREA = {"north": 44, "west": -9.5, "south": 36, "east": 4.5}
GRID_RESOLUTION = 0.25  # resolucion nativa ERA5

CDS_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "100m_u_component_of_wind",
    "100m_v_component_of_wind",
    "instantaneous_10m_wind_gust",
    "surface_solar_radiation_downwards",
    "total_cloud_cover",
    "total_precipitation",
    "mean_sea_level_pressure",
]
# NOTA: "surface_solar_radiation_downward_clear_sky" (ssrdc) se elimino
# (10-ago-2026) -- ERA5 si la tiene, pero ECMWF Open Data NO la ofrece
# ("No index entries for param=ssrdc"), asi que jamas estaria disponible
# en produccion. Mantenerla solo en el historico crearia un canal que el
# modelo ve en entrenamiento pero nunca en inferencia real (train/serve
# skew) -- se prefiere no tenerla en ninguno de los dos tensores.

# Orden y unidades del tensor .npy (mismo orden usado en process_month y en
# ecmwf_forecast_load.py, para que ambos tensores sean compatibles):
#   t2m: K | d2m: K | u10/v10/u100/v100: m/s | wind_gust10: m/s |
#   ssrd: W/m2 (convertido) | tcc: fraccion 0-1 | tp: mm (convertido) | msl: Pa
TENSOR_VAR_ORDER = ["t2m", "d2m", "u10", "v10", "u100", "v100", "wind_gust10",
                    "ssrd", "tcc", "tp", "msl"]
# Nombres netCDF alternativos para wind gust segun version del conversor CDS
GUST_VAR_CANDIDATES = ["i10fg", "fg10"]

DB_TABLE = "era5_weather_agg"
DB_COLUMNS = [
    "t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean", "wind_gust10_mean",
    "ssrd_mean", "tcc_mean", "tp_mean", "msl_mean",
]

START_MONTH_DEFAULT = "2020-01"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("era5_historical_load")


# ── BD helpers (mismo criterio anti-duplicados que commodities_load.py) ────────

def ensure_table(conn):
    """Crea la tabla si no existe; si ya existe con un esquema mas viejo,
    añade las columnas que falten (no borra ni toca datos existentes)."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {DB_TABLE} (ts TIMESTAMP NOT NULL PRIMARY KEY);")
        all_cols = {c: "DOUBLE PRECISION" for c in DB_COLUMNS}
        all_cols["tensor_path"] = "TEXT"
        all_cols["tensor_index"] = "INTEGER"
        for col, coltype in all_cols.items():
            cur.execute(f"ALTER TABLE {DB_TABLE} ADD COLUMN IF NOT EXISTS {col} {coltype};")
    conn.commit()


def get_existing_timestamps(conn, start, end) -> set:
    """Timestamps que ya existen en la tabla para el rango dado."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT ts FROM {DB_TABLE} WHERE ts >= %s AND ts <= %s", (start, end))
        return {row[0] for row in cur.fetchall()}


def get_incomplete_timestamps(conn, start, end) -> set:
    """Timestamps existentes con NULL en alguna columna meteorologica (recarga parcial previa)."""
    null_check = " OR ".join(f"{c} IS NULL" for c in DB_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT ts FROM {DB_TABLE} WHERE ts >= %s AND ts <= %s AND ({null_check})",
            (start, end),
        )
        return {row[0] for row in cur.fetchall()}


def insert_new_rows(conn, records: list) -> int:
    """INSERT filas nuevas completas — ON CONFLICT DO NOTHING como doble proteccion."""
    if not records:
        return 0
    sql = f"""
        INSERT INTO {DB_TABLE} (ts, {", ".join(DB_COLUMNS)}, tensor_path, tensor_index)
        VALUES %s
        ON CONFLICT (ts) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def update_incomplete_rows(conn, records: list) -> int:
    """UPDATE solo columnas NULL en timestamps existentes (equivalente a update_nulls())."""
    if not records:
        return 0
    set_clause = ", ".join(f"{c} = COALESCE({c}, %s)" for c in DB_COLUMNS)
    sql = (
        f"UPDATE {DB_TABLE} SET {set_clause}, "
        f"tensor_path = COALESCE(tensor_path, %s), tensor_index = COALESCE(tensor_index, %s) "
        f"WHERE ts = %s"
    )
    updated = 0
    with conn.cursor() as cur:
        for row in records:
            weather_values = row[1:-2]
            tensor_path = row[-2]
            tensor_index = row[-1]
            ts = row[0]
            cur.execute(sql, (*weather_values, tensor_path, tensor_index, ts))
            updated += cur.rowcount
    conn.commit()
    return updated


def overwrite_rows(conn, records: list) -> int:
    """UPDATE incondicional (pisa cualquier valor previo, incluido tensor_path/tensor_index).
    Usar SOLO con --force: COALESCE no sirve aqui porque una fila ya completa
    con tensor_path apuntando a un archivo viejo/movido nunca se actualizaria."""
    if not records:
        return 0
    set_clause = ", ".join(f"{c} = %s" for c in DB_COLUMNS)
    sql = f"UPDATE {DB_TABLE} SET {set_clause}, tensor_path = %s, tensor_index = %s WHERE ts = %s"
    updated = 0
    with conn.cursor() as cur:
        for row in records:
            weather_values = row[1:-2]
            tensor_path = row[-2]
            tensor_index = row[-1]
            ts = row[0]
            cur.execute(sql, (*weather_values, tensor_path, tensor_index, ts))
            updated += cur.rowcount
    conn.commit()
    return updated


# ── Descarga y procesado ERA5 ───────────────────────────────────────────────────

def month_range(start: str, end: str):
    start_dt = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    cur = start_dt
    while cur <= end_dt:
        yield cur.strftime("%Y-%m")
        year, month = cur.year, cur.month + 1
        if month > 12:
            month = 1
            year += 1
        cur = datetime(year, month, 1)


def download_month(client: "cdsapi.Client", year_month: str, nc_path: Path, max_retries=3):
    year, month = year_month.split("-")
    days = [f"{d:02d}" for d in range(1, monthrange(int(year), int(month))[1] + 1)]
    request = {
        "product_type": ["reanalysis"],
        "variable": CDS_VARIABLES,
        "year": [year],
        "month": [month],
        "day": days,
        # Cada 3h (00,03,...,21 UTC), NO horario -- alineado con la
        # granularidad real disponible en ECMWF Open Data (ver
        # ecmwf_forecast_load.py y METEO_README.md, seccion "Ventana de
        # contexto"/granularidad). Mismas horas UTC exactas en ambas fuentes.
        "time": [f"{h:02d}:00" for h in range(0, 24, 3)],
        "area": [AREA["north"], AREA["west"], AREA["south"], AREA["east"]],
        "grid": [GRID_RESOLUTION, GRID_RESOLUTION],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"  Descargando ERA5 {year_month} (intento {attempt}/{max_retries})...")
            client.retrieve("reanalysis-era5-single-levels", request, str(nc_path))
            return
        except Exception as e:
            log.warning(f"  Fallo descarga {year_month} intento {attempt}: {e}")
            time.sleep(30 * attempt)
    raise RuntimeError(f"No se pudo descargar {year_month} tras {max_retries} intentos")


def _normalize_dims(ds: xr.Dataset) -> xr.Dataset:
    """
    CDS a veces devuelve 'valid_time' en vez de 'time', y dimensiones extra:
    'number' (miembro de ensemble, size 1 en reanalisis determinista) y
    'expver' (ERA5 final vs ERA5T preliminar, aparece al pedir meses muy
    recientes que aun no tienen version final publicada).
    """
    if "number" in ds.dims:
        ds = ds.isel(number=0, drop=True)
    if "expver" in ds.dims:
        if ds.sizes["expver"] > 1:
            # Priorizar ERA5 final (expver=1) sobre ERA5T preliminar (expver=5
            # u otros) donde ambas existan; sortby asegura que el final vaya primero.
            ds = ds.sortby("expver")
            vals = list(ds["expver"].values)
            base = ds.sel(expver=vals[0]).drop_vars("expver")
            for v in vals[1:]:
                base = base.combine_first(ds.sel(expver=v).drop_vars("expver"))
            ds = base
        else:
            ds = ds.isel(expver=0, drop=True)
    if "valid_time" in ds.variables and "time" not in ds.variables:
        ds = ds.rename({"valid_time": "time"})
    return ds


def open_era5_dataset(nc_path: Path) -> xr.Dataset:
    """
    Abre el archivo descargado de CDS. Aunque se pida "download_format":
    "unarchived", CDS a veces devuelve un ZIP de todas formas cuando la
    peticion mezcla variables acumuladas (ssrd, ssrdc, tp) e instantaneas
    (t2m, d2m, viento, gust, tcc, msl): las separa internamente en dos
    NetCDF (data_stream-oper_stepType-accum.nc / ...-instant.nc) y las
    empaqueta en un ZIP. Comportamiento conocido de CDS (ver foro ECMWF),
    no controlable desde los parametros del request.
    """
    if zipfile.is_zipfile(nc_path):
        with tempfile.TemporaryDirectory() as tmp_extract:
            with zipfile.ZipFile(nc_path) as zf:
                zf.extractall(tmp_extract)
                extracted = [Path(tmp_extract) / n for n in zf.namelist()]
            datasets = []
            for f in extracted:
                if f.suffix == ".nc":
                    with xr.open_dataset(f) as ds_tmp:
                        datasets.append(ds_tmp.load())  # cierra el handle al salir del "with"
            if not datasets:
                raise RuntimeError(f"ZIP sin archivos .nc dentro: {nc_path} (contenido: {extracted})")
            return _normalize_dims(xr.merge(datasets, compat="override"))
    return _normalize_dims(xr.open_dataset(nc_path))


def _gust_var_name(ds: xr.Dataset):
    for name in GUST_VAR_CANDIDATES:
        if name in ds.variables:
            return name
    return None


def process_month(nc_path: Path, year_month: str):
    """NetCDF (o ZIP, ver open_era5_dataset) -> tensor .npy + filas para la tabla."""
    ds = open_era5_dataset(nc_path)
    gust_var = _gust_var_name(ds)

    wind10 = np.sqrt(ds["u10"] ** 2 + ds["v10"] ** 2) if "u10" in ds and "v10" in ds else None
    wind100 = np.sqrt(ds["u100"] ** 2 + ds["v100"] ** 2) if "u100" in ds and "v100" in ds else None

    # Construir el tensor en el orden fijo de TENSOR_VAR_ORDER, aplicando las
    # conversiones de unidad necesarias antes de apilar:
    #   ssrd: J/m2 acumulado en la hora -> W/m2 (dividir entre 3600)
    #   tp: metros acumulados en la hora -> mm (multiplicar por 1000)
    #   t2m, d2m, msl: sin conversion (K, K, Pa crudos)
    #   wind10/wind100: modulo del vector, no viene directo de CDS
    tensor_layers = {
        "t2m": ds["t2m"].values if "t2m" in ds else None,
        "d2m": ds["d2m"].values if "d2m" in ds else None,
        "u10": ds["u10"].values if "u10" in ds else None,
        "v10": ds["v10"].values if "v10" in ds else None,
        "u100": ds["u100"].values if "u100" in ds else None,
        "v100": ds["v100"].values if "v100" in ds else None,
        "wind_gust10": ds[gust_var].values if gust_var else None,
        "ssrd": (ds["ssrd"].values / 3600.0) if "ssrd" in ds else None,
        "tcc": ds["tcc"].values if "tcc" in ds else None,
        "tp": (ds["tp"].values * 1000.0) if "tp" in ds else None,
        "msl": ds["msl"].values if "msl" in ds else None,
    }
    available_vars = [v for v in TENSOR_VAR_ORDER if tensor_layers.get(v) is not None]
    tensor = np.stack([tensor_layers[v] for v in available_vars], axis=-1)

    TENSOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tensor_path = TENSOR_OUTPUT_DIR / f"era5_tensor_{year_month}.npy"
    np.save(tensor_path, tensor.astype(np.float32))
    tensor_path = str(tensor_path)

    rows = []
    for i, ts in enumerate(ds["time"].values):
        rows.append((
            str(ts),
            float(ds["t2m"].isel(time=i).mean()) if "t2m" in ds else None,
            float(ds["d2m"].isel(time=i).mean()) if "d2m" in ds else None,
            float(wind10.isel(time=i).mean()) if wind10 is not None else None,
            float(wind100.isel(time=i).mean()) if wind100 is not None else None,
            float(ds[gust_var].isel(time=i).mean()) if gust_var else None,
            float(ds["ssrd"].isel(time=i).mean()) / 3600 if "ssrd" in ds else None,
            float(ds["tcc"].isel(time=i).mean()) if "tcc" in ds else None,
            float(ds["tp"].isel(time=i).mean()) * 1000 if "tp" in ds else None,
            float(ds["msl"].isel(time=i).mean()) if "msl" in ds else None,
            tensor_path,
            i,
        ))
    ds.close()
    return rows, tensor_path


def load_month(client, year_month: str, tmp_dir: Path, conn, force: bool, db_config: dict):
    """Devuelve la conexion (reconectada si hacia falta), para que el caller la reutilice."""
    if conn.closed:
        log.warning("Conexion a BD cerrada, reconectando...")
        conn = psycopg2.connect(**db_config)

    nc_path = tmp_dir / f"era5_{year_month}.nc"
    try:
        y, m = year_month.split("-")
        expected_hours = monthrange(int(y), int(m))[1] * 8  # 8 marcas/dia (cada 3h), no 24
        start_ts = f"{year_month}-01 00:00:00"
        end_ts = f"{year_month}-{monthrange(int(y), int(m))[1]:02d} 23:00:00"

        existing = get_existing_timestamps(conn, start_ts, end_ts)
        incomplete = get_incomplete_timestamps(conn, start_ts, end_ts)

        if not force and len(existing) == expected_hours and not incomplete:
            log.info(f"{year_month} ya completo en BD ({len(existing)}/{expected_hours}), se omite")
            return conn

        download_month(client, year_month, nc_path)
        rows, tensor_path = process_month(nc_path, year_month)

        new_records = [r for r in rows if r[0] not in existing]
        if force:
            # Con --force, las filas ya existentes (completas o no) se
            # sobrescriben sin condicion -- necesario para refrescar
            # tensor_path/tensor_index si apuntaban a un archivo viejo/movido.
            overwrite_records = [r for r in rows if r[0] in existing]
            ins = insert_new_rows(conn, new_records)
            upd = overwrite_rows(conn, overwrite_records)
        else:
            update_records = [r for r in rows if r[0] in incomplete]
            ins = insert_new_rows(conn, new_records)
            upd = update_incomplete_rows(conn, update_records)

        log.info(f"  {year_month}: {len(new_records)} timestamps nuevos, tensor en {tensor_path}")
        log.info(f"  {year_month}: {ins} insertados, {upd} actualizados")
    except Exception as e:
        log.error(f"Error cargando {year_month}: {e}")
    finally:
        if nc_path.exists():
            nc_path.unlink()

    return conn


def main():
    parser = argparse.ArgumentParser(description="Carga historica ERA5 -> tensor + PostgreSQL")
    parser.add_argument("--start", default=START_MONTH_DEFAULT, help="YYYY-MM")
    parser.add_argument("--end", default=date.today().strftime("%Y-%m"), help="YYYY-MM")
    parser.add_argument("--force", action="store_true", help="Forzar recarga aunque el mes ya este completo")
    parser.add_argument("--tmp-dir", default="/tmp/era5_downloads")
    args = parser.parse_args()

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")
    ensure_table(conn)

    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    cds_key = load_cds_key()
    client = cdsapi.Client(url=CDS_URL, key=cds_key)

    for ym in month_range(args.start, args.end):
        conn = load_month(client, ym, tmp_dir, conn, args.force, db_config)

    conn.close()
    log.info("DONE")


if __name__ == "__main__":
    main()