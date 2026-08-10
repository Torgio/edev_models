"""
verificar_compatibilidad.py
Comprueba que el tensor historico (ERA5) y el de pronostico (ECMWF Open
Data) son compatibles para alimentar el mismo modelo: mismo orden/numero
de variables, y que la tabla historica ya este unificada a granularidad
de 3h (ver METEO_README.md, seccion "Granularidad").

No compara valores fila a fila entre ambas tablas -- no hay overlap real
de fechas entre reanalisis (retrasado) y pronostico (futuro), asi que no
tiene sentido esperar timestamps en comun.

Uso:
    python verificar_compatibilidad.py
"""

import glob
import os

import numpy as np
import psycopg2

from config import load_config
import era5_load as era5
import ecmwf_forecast_load as ecmwf


def check_variable_order():
    print("== 1. Orden y numero de variables del tensor ==")
    print("  ERA5  TENSOR_VAR_ORDER :", era5.TENSOR_VAR_ORDER)
    print("  ECMWF TENSOR_VAR_ORDER :", ecmwf.TENSOR_VAR_ORDER)
    if era5.TENSOR_VAR_ORDER == ecmwf.TENSOR_VAR_ORDER:
        print("  OK: identico en ambos scripts\n")
        return True
    print("  ERROR: no coinciden -- el tensor de pronostico y el historico "
          "tendrian canales en distinto orden/cantidad\n")
    return False


def check_tensor_shapes():
    print("== 2. Shape de un tensor de muestra de cada fuente ==")
    era5_files = sorted(glob.glob(str(era5.TENSOR_OUTPUT_DIR / "*.npy")))
    ecmwf_files = sorted(glob.glob(str(ecmwf.TENSOR_OUTPUT_DIR / "*.npy")))

    if not era5_files:
        print(f"  AVISO: no hay tensores ERA5 todavia en {era5.TENSOR_OUTPUT_DIR}")
    if not ecmwf_files:
        print(f"  AVISO: no hay tensores ECMWF todavia en {ecmwf.TENSOR_OUTPUT_DIR}")
    if not era5_files or not ecmwf_files:
        print()
        return None

    t_era5 = np.load(era5_files[-1])
    t_ecmwf = np.load(ecmwf_files[-1])
    print(f"  ERA5  ({os.path.basename(era5_files[-1])}):  shape {t_era5.shape}")
    print(f"  ECMWF ({os.path.basename(ecmwf_files[-1])}): shape {t_ecmwf.shape}")

    n_vars_era5 = t_era5.shape[-1]
    n_vars_ecmwf = t_ecmwf.shape[-1]
    if n_vars_era5 == n_vars_ecmwf:
        print(f"  OK: mismo numero de canales ({n_vars_era5})\n")
    else:
        print(f"  ERROR: distinto numero de canales ({n_vars_era5} vs {n_vars_ecmwf})\n")
    return n_vars_era5 == n_vars_ecmwf


def check_era5_granularidad(conn):
    """Detecta si era5_weather_agg todavia tiene horas mezcladas (24/dia
    viejo + 3h/dia nuevo) -- pendiente de recarga con --force."""
    print("== 3. Granularidad de era5_weather_agg (tabla historica) ==")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE EXTRACT(HOUR FROM ts)::int % 3 != 0) AS horas_no_multiplo_3,
                COUNT(*) AS total
            FROM era5_weather_agg
        """)
        no_mult3, total = cur.fetchone()

    if total == 0:
        print("  AVISO: la tabla esta vacia\n")
        return None

    pct = 100 * no_mult3 / total
    print(f"  Filas totales: {total}")
    print(f"  Filas en horas que NO son multiplo de 3 (resto de la granularidad vieja): {no_mult3} ({pct:.1f}%)")
    if no_mult3 == 0:
        print("  OK: toda la tabla ya esta en granularidad de 3h\n")
        return True
    print("  PENDIENTE: hay filas con granularidad horaria vieja mezclada con "
          "la nueva de 3h -- recargar con --force (ver METEO_README.md, "
          "seccion 6, pendiente bloqueante antes de entrenar)\n")
    return False


def check_overlap_fechas(conn):
    print("== 4. Overlap de fechas entre ambas tablas (informativo) ==")
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(ts), MAX(ts) FROM era5_weather_agg")
        era5_min, era5_max = cur.fetchone()
        cur.execute("SELECT MIN(ts), MAX(ts) FROM ecmwf_forecast_agg")
        ecmwf_min, ecmwf_max = cur.fetchone()

    print(f"  ERA5  (historico):  {era5_min} -> {era5_max}")
    print(f"  ECMWF (pronostico): {ecmwf_min} -> {ecmwf_max}")
    if era5_max and ecmwf_min and era5_max >= ecmwf_min:
        print("  Hay overlap -- se podria comparar valores reales vs pronosticados para esas fechas.\n")
    else:
        print("  Sin overlap (esperado: ERA5 llega hasta el pasado reciente, "
              "ECMWF Open Data solo mira hacia el futuro desde hoy).\n")


def main():
    ok_orden = check_variable_order()
    ok_shapes = check_tensor_shapes()

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)
    try:
        ok_granularidad = check_era5_granularidad(conn)
        check_overlap_fechas(conn)
    finally:
        conn.close()

    print("== Resumen ==")
    print(f"  Orden de variables coincide: {ok_orden}")
    print(f"  Shape de tensores coincide:  {ok_shapes}")
    print(f"  ERA5 ya unificado a 3h:      {ok_granularidad}")


if __name__ == "__main__":
    main()