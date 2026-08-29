"""Backfill historico de los TENSORES de prevision ECMWF (campo espacial, no agregado).

Hermano de `ecmwf_forecast_historico.py`. Aquel rellena las columnas `*_mean` de
`ecmwf_forecast_agg` -- la media sobre la peninsula, un numero por hora. Este rellena lo
otro: el `.npy` con el CAMPO completo y su `tensor_path` / `tensor_index` en la tabla,
que es lo que consume la CNN.

Comparte con el otro modulo la fuente (Open-Meteo Previous Runs, ECMWF IFS 0.25 grados),
la antelacion, el dominio y las conversiones de unidades. Se importan de alli para que no
puedan divergir.

QUE CAMBIA RESPECTO AL TENSOR DEL CRON -- leer antes de mezclarlos
El cron (`ecmwf_forecast_load.py`) baja un GRIB y guarda `(8, 33, 57, 11)` por run_date:
8 pasos trihorarios, rejilla de 0.25 grados. Este script produce `(24, n_lat, n_lon, 11)`:

  1. EJE TEMPORAL: 24 pasos HORARIOS, no 8 trihorarios. Open-Meteo sirve hora a hora y
     no tiene sentido tirar dos de cada tres. `tensor_index` sigue apuntando a la fila
     correspondiente, asi que cada fuente es coherente consigo misma.
  2. REJILLA: la marca `--paso`. A 0.25 sale `(33, 57)`, identica a la del cron y a la de
     ERA5. Mas gruesa, sale mas pequena -- y a 1 grado sale EXACTAMENTE ERA5[::4, ::4],
     porque 36..44 cada 0.25 son 33 puntos y uno de cada cuatro da 36, 37, ..., 44.
     No es un apano incompatible: es la misma rejilla submuestreada, alineada celda a
     celda, y permite comparar las dos resoluciones como ablacion.

Por eso el nombre del fichero lleva fuente y paso: un consumidor que cargue por
`tensor_path` no puede llevarse una sorpresa de forma.

COSTE (Open-Meteo cobra por localizaciones x dias; cupo 5.000/hora, 10.000/dia):
    paso 0.25  ->  1.881 celdas  ->  ~118.500 unidades  ->  ~12 dias de reloj
    paso 0.5   ->    493 celdas  ->   ~31.000 unidades  ->   3-4 dias
    paso 1.0   ->    135 celdas  ->    ~8.500 unidades  ->   UN DIA
Las descargas comparten cuota: no lanzar dos a la vez, se ahogan entre si.

ORDEN DE CANALES: el de `TENSOR_VAR_ORDER` de los dos loaders del cron, para que un
modelo entrenado con unos tensores pueda leer los otros.
    [t2m, d2m, u10, v10, u100, v100, wind_gust10, ssrd, tcc, tp, msl]
`wind_gust10` va a NaN: no esta archivada en Previous Runs (verificado 28-ago-2026).
Es el mismo caso que `ssrdc` en el cron -- canal presente y vacio, por compatibilidad.

Uso:
    python ecmwf_tensor_historico.py --a-postgres              # 0.25 grados, 24 pasos
    python ecmwf_tensor_historico.py --a-postgres --cupo 8000  # para en ese consumo
    python ecmwf_tensor_historico.py --trihorario              # 8 pasos, como el cron
    python ecmwf_tensor_historico.py --paso 1.0                # rejilla gruesa, prototipo

CRON DIARIO -- es la forma prevista de correrlo
El historico completo son ~118.500 unidades de API y el cupo de Open-Meteo son 10.000 al
dia, asi que no cabe en una sesion: son unos 12 dias naturales. El script esta hecho para
eso -- cada corrida gasta su `--cupo` y para sola, y la siguiente detecta los `.npy` que
ya estan en disco y sigue donde lo dejo. No hay que pasarle fechas.

    30 2 * * * /home/ubuntu/tfm-env/bin/python -u /home/ubuntu/scripts/ingesta/ecmwf_tensor_historico.py --a-postgres --cupo 9000 >> /home/ubuntu/scripts/logs/cron_ecmwf_tensor.log 2>&1

El `-u` es imprescindible: sin el, Python acumula la salida al redirigir a fichero y el
log parece vacio durante horas aunque el proceso este trabajando.

A las 02:30 y no a las 00:30 porque el crontab del servidor lleva `CRON_TZ=Europe/Madrid`:
las 00:30 de Madrid son las 22:30 o 23:30 UTC, o sea el dia ANTERIOR, con el cupo diario
de Open-Meteo probablemente ya gastado. Las 02:30 de Madrid caen ya en el dia UTC nuevo
en cualquier epoca del año. `--cupo 9000` deja un margen
de 1.000 unidades por si algo mas tira de la misma API. Cuando el log diga "Tramos
descargados: 0" varios dias seguidos, el historico esta completo y el cron se puede
retirar (o dejarlo: no hace nada y no cuesta).

AVISO sobre los limites: ademas del cupo diario hay uno POR MINUTO (600 unidades). Con
1.881 celdas en lotes de 25, cada tramo son 76 peticiones y ~1.881 unidades, asi que el
minimo teorico son ~3 minutos por tramo. El script lo absorbe esperando, pero por eso una
corrida diaria tarda horas: es normal, no esta colgado.
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
# `meteo_horario` vive en scripts/: de ahi sale la lista de horas que publica
# ECMWF Open Data, la misma que usa la reconstruccion de la matriz.
sys.path.append(str(Path(__file__).resolve().parent.parent / "scripts"))
from ecmwf_forecast_historico import (  # noqa: E402
    AREA, API, INICIO_ARCHIVO, LEAD_DIAS, MODELO, PAUSA_S, REPO, TABLA,
    _pedir, _tramos, conexion, preparar_tabla,
)
from meteo_horario import HORAS_PRODUCCION  # noqa: E402

import time  # noqa: E402

TENSOR_DIR = REPO / "data" / "ecmwf_forecast_tensors"

# Orden de canales del cron. No tocar sin tocar los dos loaders.
TENSOR_VAR_ORDER = ["t2m", "d2m", "u10", "v10", "u100", "v100", "wind_gust10",
                    "ssrd", "tcc", "tp", "msl"]

# Variables que hay que pedirle a Open-Meteo para poder construir esos 11 canales.
# El viento se pide como VELOCIDAD + DIRECCION porque el tensor guarda componentes u/v
# y la API no las sirve directamente.
PEDIDAS = ["temperature_2m", "dew_point_2m",
           "wind_speed_10m", "wind_direction_10m",
           "wind_speed_100m", "wind_direction_100m",
           "shortwave_radiation", "cloud_cover", "precipitation", "pressure_msl"]

PUNTOS_POR_LOTE = 25
# Open-Meteo cobra `localizaciones x ceil(dias/14)`: un tramo de 7 dias cuesta LO MISMO
# que uno de 14, asi que pedir semanas duplicaba el gasto para nada. El tensor se sigue
# guardando por run_date, la granularidad del fichero no depende del tamano del tramo.
DIAS_POR_LOTE = 14


def rejilla_tensor(paso: float):
    """Puntos del dominio en orden de rejilla, LATITUD DESCENDENTE.

    Norte->sur es la convencion de CDS y por tanto la de los tensores de ERA5. Generar
    de sur a norte daria un campo volteado verticalmente, que es el tipo de fallo que no
    da error y arruina el entrenamiento en silencio.
    """
    lats = np.arange(AREA["north"], AREA["south"] - 1e-9, -paso)
    lons = np.arange(AREA["west"], AREA["east"] + 1e-9, paso)
    puntos = [(round(float(la), 4), round(float(lo), 4)) for la in lats for lo in lons]
    return puntos, len(lats), len(lons)


def _uv(velocidad, direccion):
    """Velocidad + direccion meteorologica -> componentes u (este) y v (norte).

    La direccion es de DONDE VIENE el viento, de ahi los signos negativos. Comprobacion
    mental: viento del oeste (270 grados) -> u positivo, v cero; sopla hacia el este.
    """
    rad = np.deg2rad(direccion)
    return -velocidad * np.sin(rad), -velocidad * np.cos(rad)


def _canales(bloque: dict) -> dict:
    """De las variables crudas de Open-Meteo a los 11 canales del tensor, en sus unidades."""
    u10, v10 = _uv(bloque["wind_speed_10m"], bloque["wind_direction_10m"])
    u100, v100 = _uv(bloque["wind_speed_100m"], bloque["wind_direction_100m"])
    return {
        "t2m": bloque["temperature_2m"] + 273.15,          # C -> K
        "d2m": bloque["dew_point_2m"] + 273.15,
        "u10": u10, "v10": v10, "u100": u100, "v100": v100,  # m/s (se piden asi)
        "wind_gust10": np.full_like(u10, np.nan),           # no archivada
        "ssrd": bloque["shortwave_radiation"],               # W/m2
        "tcc": bloque["cloud_cover"] / 100.0,                # % -> fraccion
        "tp": bloque["precipitation"],                       # mm
        "msl": bloque["pressure_msl"] * 100.0,               # hPa -> Pa
    }


def descargar_rejilla(ini: date, fin: date, puntos, n_lat, n_lon, lead: int):
    """Devuelve (horas, array (n_horas, n_lat, n_lon, 11)) para el tramo pedido."""
    sufijo = f"_previous_day{lead}"
    lotes = [puntos[i:i + PUNTOS_POR_LOTE] for i in range(0, len(puntos), PUNTOS_POR_LOTE)]
    crudo, horas = {v: {} for v in PEDIDAS}, None

    for lote in lotes:
        lat = ",".join(str(p[0]) for p in lote)
        lon = ",".join(str(p[1]) for p in lote)
        url = (f"{API}?latitude={lat}&longitude={lon}"
               f"&hourly={','.join(v + sufijo for v in PEDIDAS)}"
               f"&start_date={ini}&end_date={fin}"
               f"&models={MODELO}&timezone=UTC&wind_speed_unit=ms")
        datos = _pedir(url)
        if isinstance(datos, dict):
            datos = [datos]
        for d, punto in zip(datos, lote):
            h = d["hourly"]
            horas = h["time"] if horas is None else horas
            for v in PEDIDAS:
                crudo[v][punto] = h.get(f"{v}{sufijo}")
        time.sleep(PAUSA_S)

    n_h = len(horas)
    bloque = {}
    for v in PEDIDAS:
        m = np.full((n_h, len(puntos)), np.nan, dtype=np.float32)
        for j, punto in enumerate(puntos):
            serie = crudo[v].get(punto)
            if serie is not None:
                m[:, j] = [np.nan if x is None else x for x in serie]
        bloque[v] = m

    canales = _canales(bloque)
    tensor = np.stack([canales[c] for c in TENSOR_VAR_ORDER], axis=-1)   # (n_h, puntos, 11)
    return horas, tensor.reshape(n_h, n_lat, n_lon, len(TENSOR_VAR_ORDER))


def guardar_por_run_date(horas, tensor, paso: float, lead: int,
                         solo_horas_produccion: bool = True):
    """Un .npy por run_date, como el cron. Devuelve filas (ts, run_date, path, indice).

    `solo_horas_produccion` deja los 8 pasos trihorarios (0, 3, 6... 21) en vez de las 24
    horas. Es lo coherente con el resto del proyecto: ECMWF Open Data -- lo que alimentara
    produccion -- solo publica cada 3 horas, y guardar 24 pasos en el historico daria un
    tensor que produccion no puede reproducir. Ademas asi la forma es `(8, n_lat, n_lon, 11)`,
    identica a la del cron y a la de ERA5, y el fichero baja de 1.940 a 647 KB/dia.
    """
    TENSOR_DIR.mkdir(parents=True, exist_ok=True)
    ts = pd.to_datetime(horas, utc=True)

    if solo_horas_produccion:
        mant = np.isin(ts.hour, HORAS_PRODUCCION)
        ts, tensor = ts[mant], tensor[mant]

    run_date = (ts - pd.Timedelta(days=lead)).date

    filas = []
    for rd in sorted(set(run_date)):
        sel = np.flatnonzero(run_date == rd)
        sufijo = "3h" if solo_horas_produccion else "1h"
        ruta = TENSOR_DIR / f"ecmwf_fc_openmeteo_{paso}deg_{sufijo}_d{lead}_{rd}.npy"
        np.save(ruta, tensor[sel].astype(np.float32))
        for i, k in enumerate(sel):
            filas.append((ts[k].tz_localize(None).to_pydatetime(), rd, str(ruta), i))
    return filas


def escribir_rutas(con, filas):
    """Solo actualiza `tensor_path` de filas que YA existen y no lo tienen.

    No inserta: las filas de agregados las crea `ecmwf_forecast_historico.py`. Y usa
    `tensor_path IS NULL` para no pisar nunca las del cron, que apuntan a su propio GRIB.
    """
    from psycopg2.extras import execute_values
    with con.cursor() as cur:
        execute_values(cur, f"""
            UPDATE {TABLA} t SET tensor_path = d.path, tensor_index = d.idx
            FROM (VALUES %s) AS d(ts, run_date, path, idx)
            WHERE t.ts = d.ts AND t.run_date = d.run_date AND t.tensor_path IS NULL
        """, filas, template="(%s::timestamp, %s::date, %s, %s::int)", page_size=2000)
        n = cur.rowcount
    con.commit()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", default=str(INICIO_ARCHIVO))
    ap.add_argument("--hasta", default=str(date.today()))
    # 0.25 por defecto, que es la resolucion de los tensores de ERA5 y de los del cron.
    # La regla es guardar al maximo disponible y submuestrear al entrenar: de 0.25 a 1
    # grado es `tensor[:, ::4, ::4, :]`, instantaneo; al reves no se puede. Guardar a 1
    # grado ahorraria 11 dias de descarga a cambio de una perdida irreversible.
    ap.add_argument("--paso", type=float, default=0.25,
                    help="grados de la rejilla: 0.25 = 33x57 como ERA5 (~12 dias); "
                         "1.0 = 9x15 (un dia, solo para prototipar)")
    ap.add_argument("--lead", type=int, default=LEAD_DIAS, choices=[1, 2, 3])
    ap.add_argument("--a-postgres", action="store_true")
    # Horario POR DEFECTO. Se guarda al maximo que da la fuente y se submuestrea al
    # entrenar: `tensor[::3]` da los 8 pasos que reproducira produccion, y al reves no
    # hay vuelta. Mismo criterio que con la rejilla (0.25 grados, no 1).
    ap.add_argument("--trihorario", action="store_true",
                    help="guardar solo los 8 pasos de produccion en vez de las 24 horas")
    ap.add_argument("--cupo", type=int, default=9000,
                    help="unidades de API antes de parar (cupo diario de Open-Meteo: 10.000)")
    a = ap.parse_args()

    desde = max(date.fromisoformat(a.desde), INICIO_ARCHIVO)
    hasta = date.fromisoformat(a.hasta)
    puntos, n_lat, n_lon = rejilla_tensor(a.paso)
    tramos = list(_tramos(desde, hasta, DIAS_POR_LOTE))

    print(f"Dominio {AREA} · paso {a.paso}° -> rejilla {n_lat} x {n_lon} = {len(puntos)} celdas")
    pasos = len(HORAS_PRODUCCION) if a.trihorario else 24
    print(f"Tensor por run_date: ({pasos}, {n_lat}, {n_lon}, {len(TENSOR_VAR_ORDER)}) float32 "
          f"= {pasos * n_lat * n_lon * 11 * 4 / 1024:.0f} KB/dia"
          f"{'  (8 pasos trihorarios, como el cron)' if a.trihorario else '  (24 pasos horarios)'}")
    print(f"Rango {desde} -> {hasta} · {len(tramos)} tramos de {DIAS_POR_LOTE} dias")
    print(f"Coste estimado: {len(tramos) * len(puntos):,} unidades · cupo de esta corrida: {a.cupo:,}")
    print(f"Salida: {TENSOR_DIR}")

    con = None
    if a.a_postgres:
        con = conexion()
        preparar_tabla(con)

    gastado, hechos, rutas = 0, 0, 0
    t0 = time.time()
    try:
        for ti, (ini, fin) in enumerate(tramos, 1):
            # Si el tramo ya tiene todos sus .npy, no se pide nada.
            dias = [ini + timedelta(days=k) for k in range((fin - ini).days + 1)]
            suf = "3h" if a.trihorario else "1h"
            if all((TENSOR_DIR / f"ecmwf_fc_openmeteo_{a.paso}deg_{suf}_d{a.lead}_{d}.npy").exists()
                   for d in dias):
                print(f"  tramo {ti}/{len(tramos)} ({ini}->{fin}) ya en disco, se omite")
                continue
            if gastado + len(puntos) > a.cupo:
                print(f"\nCupo de la corrida agotado ({gastado:,} unidades). "
                      f"Relanza manana: continua donde lo dejo.")
                break
            horas, tensor = descargar_rejilla(ini, fin, puntos, n_lat, n_lon, a.lead)
            filas = guardar_por_run_date(horas, tensor, a.paso, a.lead,
                                         solo_horas_produccion=a.trihorario)
            gastado += len(puntos)
            hechos += 1
            if con is not None:
                rutas += escribir_rutas(con, filas)
            print(f"  tramo {ti}/{len(tramos)} ({ini}->{fin}) OK · "
                  f"{len(set(f[1] for f in filas))} dias · {gastado:,} unidades · "
                  f"{time.time() - t0:.0f}s")
    finally:
        if con is not None:
            con.close()

    print(f"\nTramos descargados: {hechos} · unidades gastadas: {gastado:,}")
    if a.a_postgres:
        print(f"Filas con tensor_path actualizado: {rutas:,}")
    print(f"Ficheros .npy en {TENSOR_DIR}: {len(list(TENSOR_DIR.glob('*.npy'))) if TENSOR_DIR.exists() else 0}")
