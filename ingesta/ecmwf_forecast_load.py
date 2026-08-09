"""
ecmwf_forecast_load.py
Pronostico D+1 (ECMWF Open Data, IFS) -> tensor .npy + tabla PostgreSQL.

Rol: input de produccion (ERA5 no esta disponible para "manana"), y despues
de acumular varios dias, sirve tambien para comparar contra el ERA5 real ya
publicado y medir cuanto se degrada el modelo por incertidumbre de pronostico.

Cobertura espacial: misma AREA que era5_historical_load.py (Espana peninsular
+ Baleares) -- imprescindible para que ambos tensores sean compatibles.

Limitacion: Open Data solo mantiene una ventana movil reciente (no hay
historico largo como en CDS), y su lista de parametros es mas limitada que
el archivo completo -- "ssrdc" (radiacion cielo despejado) puede no estar
disponible; si falta, ese canal del tensor queda en NaN en vez de romper
la descarga completa.

Requisitos: pip install ecmwf-opendata cfgrib xarray numpy psycopg2-binary

Uso:
    python ecmwf_forecast_load.py                    # run de hoy 00Z
    python ecmwf_forecast_load.py --run-date 2026-07-23
    python ecmwf_forecast_load.py --force
"""

import argparse
import logging
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

warnings.filterwarnings("ignore")

try:
    from ecmwf.opendata import Client as ECMWFClient
    import cfgrib
    import xarray as xr
except ImportError:
    print("Faltan dependencias: pip install ecmwf-opendata cfgrib xarray --break-system-packages")
    sys.exit(1)

from config import load_config

AREA = {"north": 44, "west": -9.5, "south": 36, "east": 4.5}  # igual que era5_historical_load.py

# Steps horarios 23-47h: el primero (23h) solo sirve para desacumular ssrd/tp
# del primer valor real de D+1 (24h)
STEPS = list(range(23, 48))

PARAMS = ["2t", "2d", "10u", "10v", "100u", "100v", "10fg", "ssrd", "tcc", "tp", "msl"]
# Nombres netCDF/cfgrib esperados, mismo orden y unidades que era5_historical_load.py:
#   t2m/d2m: K | u10/v10/u100/v100/gust: m/s | ssrd/ssrdc: W/m2 (convertido) |
#   tcc: 0-1 | tp: mm (convertido) | msl: Pa
TENSOR_VAR_ORDER = ["t2m", "d2m", "u10", "v10", "u100", "v100", "wind_gust10",
                    "ssrd", "ssrdc", "tcc", "tp", "msl"]
GUST_VAR_CANDIDATES = ["fg10", "i10fg", "10fg"]

DB_TABLE = "ecmwf_forecast_agg"
DB_COLUMNS = ["t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean", "wind_gust10_mean",
              "ssrd_mean", "ssrdc_mean", "tcc_mean", "tp_mean", "msl_mean"]

TENSOR_OUTPUT_DIR = Path(__file__).parent / "tensors" / "ecmwf_forecast"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ecmwf_forecast_load")


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE} (
                ts TIMESTAMP NOT NULL,
                run_date DATE NOT NULL,
                t2m_mean DOUBLE PRECISION,
                d2m_mean DOUBLE PRECISION,
                wind10_mean DOUBLE PRECISION,
                wind100_mean DOUBLE PRECISION,
                wind_gust10_mean DOUBLE PRECISION,
                ssrd_mean DOUBLE PRECISION,
                ssrdc_mean DOUBLE PRECISION,
                tcc_mean DOUBLE PRECISION,
                tp_mean DOUBLE PRECISION,
                msl_mean DOUBLE PRECISION,
                tensor_path TEXT,
                tensor_index INTEGER,
                PRIMARY KEY (ts, run_date)
            );
        """)
    conn.commit()


def get_existing_timestamps(conn, run_date: str) -> set:
    with conn.cursor() as cur:
        cur.execute(f"SELECT ts FROM {DB_TABLE} WHERE run_date = %s", (run_date,))
        return {row[0] for row in cur.fetchall()}


def insert_rows(conn, records: list) -> int:
    if not records:
        return 0
    sql = f"""
        INSERT INTO {DB_TABLE} (ts, run_date, {", ".join(DB_COLUMNS)}, tensor_path, tensor_index)
        VALUES %s
        ON CONFLICT (ts, run_date) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def download_forecast(run_date: str, grib_path: Path, max_retries=3):
    client = ECMWFClient(source="ecmwf", model="ifs")
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"Descargando ECMWF IFS run {run_date} 00Z (intento {attempt}/{max_retries})...")
            client.retrieve(
                date=run_date.replace("-", ""), time=0, stream="oper", type="fc",
                param=PARAMS, step=STEPS,
                area=[AREA["north"], AREA["west"], AREA["south"], AREA["east"]],
                target=str(grib_path),
            )
            return
        except Exception as e:
            log.warning(f"Fallo descarga run {run_date} intento {attempt}: {e}")
    raise RuntimeError(f"No se pudo descargar el run {run_date} tras {max_retries} intentos")


def leer_dataset(grib_path: Path) -> xr.Dataset:
    datasets = cfgrib.open_datasets(str(grib_path))
    ds_list = []
    for ds in datasets:
        coords_drop = [c for c in ds.coords if ds[c].dims == ()]
        ds_list.append(ds.drop_vars(coords_drop))
    return xr.merge(ds_list, compat="override")


def _gust_var_name(ds):
    for name in GUST_VAR_CANDIDATES:
        if name in ds.variables:
            return name
    return None


def process_forecast(ds: xr.Dataset, run_date: str):
    """Desacumula ssrd/ssrdc/tp y arma tensor + filas, unidades crudas (sin conversion de visualizacion)."""
    gust_var = _gust_var_name(ds)
    valid_times = ds.valid_time.values
    vts_d1 = valid_times[1:]  # excluir el primer step, solo sirve para desacumular

    frames, rows = [], []
    lat_shape = ds["t2m"].isel(valid_time=0).shape

    for i, vt in enumerate(vts_d1):
        vt_prev = valid_times[i]

        def desacum(var):
            if var not in ds:
                return None
            curr = ds[var].sel(valid_time=vt).values
            prev = ds[var].sel(valid_time=vt_prev).values
            return ((curr - prev) / 3600.0).clip(min=0)

        ssrd = desacum("ssrd")
        ssrdc = desacum("ssrdc")
        tp = desacum("tp")
        tp_mm = tp * 3600 * 1000 if tp is not None else None  # de vuelta a mm acumulados en la hora

        wind10 = np.sqrt(ds["u10"].sel(valid_time=vt) ** 2 + ds["v10"].sel(valid_time=vt) ** 2) if "u10" in ds else None
        wind100 = np.sqrt(ds["u100"].sel(valid_time=vt) ** 2 + ds["v100"].sel(valid_time=vt) ** 2) if "u100" in ds else None

        layers = {
            "t2m": ds["t2m"].sel(valid_time=vt).values if "t2m" in ds else None,
            "d2m": ds["d2m"].sel(valid_time=vt).values if "d2m" in ds else None,
            "u10": ds["u10"].sel(valid_time=vt).values if "u10" in ds else None,
            "v10": ds["v10"].sel(valid_time=vt).values if "v10" in ds else None,
            "u100": ds["u100"].sel(valid_time=vt).values if "u100" in ds else None,
            "v100": ds["v100"].sel(valid_time=vt).values if "v100" in ds else None,
            "wind_gust10": ds[gust_var].sel(valid_time=vt).values if gust_var else None,
            "ssrd": ssrd, "ssrdc": ssrdc,
            "tcc": ds["tcc"].sel(valid_time=vt).values if "tcc" in ds else None,
            "tp": tp_mm,
            "msl": ds["msl"].sel(valid_time=vt).values if "msl" in ds else None,
        }
        layer_stack = [layers[v] if layers.get(v) is not None else np.full(lat_shape, np.nan) for v in TENSOR_VAR_ORDER]
        frames.append(np.stack(layer_stack, axis=-1))

        rows.append((
            str(vt), run_date,
            float(np.mean(layers["t2m"])) if layers["t2m"] is not None else None,
            float(np.mean(layers["d2m"])) if layers["d2m"] is not None else None,
            float(wind10.mean()) if wind10 is not None else None,
            float(wind100.mean()) if wind100 is not None else None,
            float(np.mean(layers["wind_gust10"])) if layers["wind_gust10"] is not None else None,
            float(np.mean(ssrd)) if ssrd is not None else None,
            float(np.mean(ssrdc)) if ssrdc is not None else None,
            float(np.mean(layers["tcc"])) if layers["tcc"] is not None else None,
            float(np.mean(tp_mm)) if tp_mm is not None else None,
            float(np.mean(layers["msl"])) if layers["msl"] is not None else None,
        ))

    tensor = np.stack(frames, axis=0)
    TENSOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tensor_path = TENSOR_OUTPUT_DIR / f"ecmwf_forecast_tensor_{run_date}.npy"
    np.save(tensor_path, tensor.astype(np.float32))

    rows = [(*r, str(tensor_path), i) for i, r in enumerate(rows)]
    return rows, str(tensor_path)


def load_forecast(run_date: str, tmp_dir: Path, conn, force: bool, db_config: dict):
    """Devuelve la conexion (reconectada si hacia falta), para que el caller la reutilice."""
    if conn.closed:
        log.warning("Conexion a BD cerrada, reconectando...")
        conn = psycopg2.connect(**db_config)

    grib_path = tmp_dir / f"ecmwf_{run_date}.grib2"
    try:
        existing = get_existing_timestamps(conn, run_date)
        if not force and len(existing) == 24:
            log.info(f"Run {run_date} ya cargado completo, se omite")
            return conn

        download_forecast(run_date, grib_path)
        ds = leer_dataset(grib_path)
        rows, tensor_path = process_forecast(ds, run_date)
        ds.close()

        new_rows = [r for r in rows if r[0] not in existing]
        ins = insert_rows(conn, new_rows)
        log.info(f"Run {run_date}: {ins} insertados, tensor en {tensor_path}")
    except Exception as e:
        log.error(f"Error cargando run {run_date}: {e}")
    finally:
        if grib_path.exists():
            grib_path.unlink()

    return conn


def main():
    parser = argparse.ArgumentParser(description="Pronostico ECMWF D+1 -> tensor + PostgreSQL")
    parser.add_argument("--run-date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tmp-dir", default="/tmp/ecmwf_downloads")
    args = parser.parse_args()

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    log.info("Connected to PostgreSQL OK")
    ensure_table(conn)

    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    conn = load_forecast(args.run_date, tmp_dir, conn, args.force, db_config)
    conn.close()
    log.info("DONE")


if __name__ == "__main__":
    main()