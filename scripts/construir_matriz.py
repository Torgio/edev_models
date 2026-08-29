"""Construye el dataset de depuracion leyendo de Postgres.

Devuelve una fila por (dia objetivo D+1, hora) con el target y todas las features, sin
filtrar: la depuracion decide despues que se queda. Envuelve al constructor del equipo
(`construir_dataset_maestro_sergio_v5`) en lugar de reescribirlo -- espina temporal, lags,
calendario, festivos y split siguen siendo suyos, asi que sus correcciones se heredan.

FILTROS DESACTIVADOS. El constructor poda antes de entregar y aqui hace falta lo crudo:

    PODAR_REDUNDANTES   = False   la redundancia se mide, no se presupone
    FILTRAR_TENDENCIAS  = False   la deriva train->validacion se mide
    CAPACIDAD_COMO_CUOTA= False   se quiere el nivel; pasar a cuota es decision de modelado
    filtrar_cobertura   = False   la cobertura se analiza
    PERIODOS_EXCLUIDOS  = []      el apagon se CONSERVA y se marca. Borrarlo deja 2025
                                  incompleta y rompe la ventana del encoder sin avisar
    EXIGIR_BLOQUES      = []      exigia `pdbc_`, que es justo lo que se retira

BLOQUE PDBC RECALCULADO. El constructor traia `pdbc_*` de la tabla derivada
`esios_pdbc_gen`, que tiene el historico roto: convierte ceros en nulos (34.643 horas
medidas contra `esios_pbf_gen`), y ademas lo agregaba a media diaria, borrando el perfil
intradiario.

El concepto era bueno -- el PDBC es el PBF menos los bilaterales, o sea la casacion limpia,
y es la casacion la que forma el precio. Lo que fallaba era la tabla. Asi que se retira ese
bloque y se recalcula desde las tres tablas del PBF (`pdbc_horario.py`), horario y con los
ceros intactos. Verificado: eolica y ciclo combinado coinciden con la tabla derivada al
0,0000; el unico bloque danado era el bombeo.

CANAL METEOROLOGICO: siempre el tiempo de D+1.

En produccion, a las 11:00 del dia D, lo unico que hay del tiempo de manana es una
PREVISION. Asi que la columna meteorologica tiene que contener siempre la misma magnitud --
el tiempo de D+1 -- y no una cosa antes de 2024 y otra despues.

    prevision ECMWF de D+1      donde existe  (2024-04 en adelante)
    pseudo-prevision de D+1     donde no      (2020 -> 2024-03)

La pseudo-prevision (`pseudo_prevision.py`) parte del ERA5 REAL de D+1 y le suma un bloque
de 24 h de error remuestreado de los errores de prevision reales medidos en el solape. No es
ruido blanco: conserva el sesgo, la autocorrelacion horaria y la correlacion entre variables.

POR QUE SE CAMBIO. Antes el tramo sin archivo se rellenaba con ERA5 de D-1, el tiempo de la
vispera como sustituto del de manana. Y eso no es una version peor de la misma variable, es
OTRA variable. Medido:

    relleno                       parecido a lo que llega en produccion
    ERA5 de D-1  (lo anterior)                  0,44
    ERA5 de D+1 / pseudo                        0,97

La prevision de ECMWF a 24 h es casi el dato real -- el viento a 100 m correlaciona 0,972
con lo que luego ocurrio -- mientras que el tiempo de anteayer correlaciona 0,44. El relleno
antiguo metia en el 65 % de las filas de entrenamiento una magnitud distinta de la que el
modelo recibe en produccion.

Y no se rellena con ERA5 crudo por el SALTO DE SESGO: la prevision de Open-Meteo va +0,74
m/s por encima de ERA5 en viento a 100 m, asi que el ERA5 sin degradar crearia un escalon el
1 de abril de 2024 que el modelo puede aprender como marcador de fecha.

METEO_RELLENO admite las tres variantes para poder compararlas. Validacion y test son 100 %
prevision real en las tres, asi que la comparacion es honesta:

    "pseudo"    ERA5 de D+1 + error remuestreado     <- variante titular
    "real_D1"   ERA5 de D+1 crudo                    <- cota superior (meteo perfecta)
    "era5_Dm1"  ERA5 de D-1                          <- lo que habia, para el contraste

La bandera `meteo_es_forecast` sigue: 1 = prevision real, 0 = relleno.

Uso:
    from construir_matriz import construir
    datos = construir()               # desde Postgres, cacheado
    datos = construir(forzar=True)    # reconstruye aunque haya cache
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "ingesta"))
sys.path.insert(0, str(REPO / "ingesta" / "dt_maestro_sergio" / "v5_master_and_models"))

CACHE = REPO / "data" / "bronze" / "matriz_cruda.parquet"

# Variables del canal. El orden fija el de las columnas `*_meteo` resultantes.
VARIABLES_METEO = ["t2m", "d2m", "wind10", "wind100", "ssrd", "tcc", "msl"]

# Como se rellena el tramo sin archivo de prevision. Ver la nota de la cabecera.
METEO_RELLENO = "pseudo"        # pseudo | real_D1 | era5_Dm1

TABLA_ERA5 = "era5_weather_agg"
TZ = "Europe/Madrid"


def _desactivar_filtros(v5, verbose=True):
    """Deja el constructor en modo crudo. Ver la nota de arriba para el porque de cada uno."""
    antes = {k: getattr(v5, k) for k in
             ("PODAR_REDUNDANTES", "FILTRAR_TENDENCIAS", "CAPACIDAD_COMO_CUOTA",
              "PERIODOS_EXCLUIDOS", "EXIGIR_BLOQUES")}
    v5.PODAR_REDUNDANTES = False
    v5.FILTRAR_TENDENCIAS = False
    v5.CAPACIDAD_COMO_CUOTA = False
    v5.PERIODOS_EXCLUIDOS = []
    v5.EXIGIR_BLOQUES = []
    if verbose:
        print("Filtros del constructor desactivados (la depuracion es nuestra):")
        for k, v in antes.items():
            print(f"    {k:22s} {v}  ->  {getattr(v5, k)}")
    return antes


def _era5_dia_objetivo(datos: pd.DataFrame) -> pd.DataFrame:
    """ERA5 real alineado al DIA OBJETIVO, no desfasado.

    El constructor entrega ERA5 como `*_met_Dm1` / `_Dm2`, que describen dias anteriores.
    Aqui hace falta el del propio D+1, asi que se lee de la tabla y se indexa por
    (`fecha_objetivo`, `hora`).
    """
    # ERA5 se publica cada 3 HORAS, no cada hora: reindexar en crudo dejaria 16 de cada 24
    # horas vacias. Se reutiliza `era5_horario.cargar_era5()`, que ya interpola con el
    # tratamiento propio de cada variable -- lineal en las suaves, con recorte a cero en la
    # radiacion -- y esta validado contra el bloque del constructor.
    from era5_horario import cargar_era5
    g = cargar_era5(verbose=False)          # ya viene con `fecha` y `hora` en hora local
    g["f"] = pd.to_datetime(g["fecha"]).dt.date
    g = g.rename(columns={"hora": "h"})
    g = g.drop_duplicates(subset=["f", "h"], keep="first").set_index(["f", "h"])
    idx = pd.MultiIndex.from_arrays([pd.to_datetime(datos["fecha_objetivo"]).dt.date,
                                     datos["hora"]])
    out = pd.DataFrame(index=datos.index)
    for v in VARIABLES_METEO:
        out[v] = g[f"{v}_mean"].reindex(idx).to_numpy()
    return out


def _canal_meteo(datos: pd.DataFrame, relleno: str = None, verbose=True) -> pd.DataFrame:
    """Un solo canal `*_meteo` con el tiempo de D+1, y su bandera.

    Donde hay archivo de prevision se usa la prevision. Donde no, se rellena segun
    `relleno` -- por defecto con pseudo-prevision, que es ERA5 del propio D+1 degradado
    con el error de prevision real medido en el solape. Ver la cabecera del modulo.
    """
    from ecmwf_horario import bloque_para_matriz as bloque_fc, cargar as cargar_fc
    from pseudo_prevision import medir_error, resumen_error, pseudo_prevision

    relleno = relleno or METEO_RELLENO
    if relleno not in ("pseudo", "real_D1", "era5_Dm1"):
        raise ValueError("relleno debe ser pseudo | real_D1 | era5_Dm1")

    fc = bloque_fc(cargar_fc(verbose=False))
    n_antes = len(datos)
    datos = datos.merge(fc, on=["fecha_objetivo", "hora"], how="left")
    assert len(datos) == n_antes, "el merge de la prevision no debe alterar las filas"

    hay_fc = datos["t2m_fc"].notna()
    datos["meteo_es_forecast"] = hay_fc.astype(int)
    presentes = [v for v in VARIABLES_METEO if f"{v}_fc" in datos.columns]

    if relleno == "era5_Dm1":
        # comportamiento anterior: el tiempo de la vispera como sustituto
        for v in presentes:
            col = f"{v}_mean_met_Dm1"
            if col in datos.columns:
                datos[f"{v}_meteo"] = datos[f"{v}_fc"].where(hay_fc, datos[col])
        if verbose:
            print(f"  canal meteo [era5_Dm1]: {len(presentes)} columnas · "
                  f"{int(hay_fc.sum()):,} de prevision, {int((~hay_fc).sum()):,} de ERA5 D-1")
        return datos

    era5 = _era5_dia_objetivo(datos)

    if relleno == "real_D1":
        for v in presentes:
            datos[f"{v}_meteo"] = datos[f"{v}_fc"].where(hay_fc, era5[v])
        if verbose:
            print(f"  canal meteo [real_D1 · COTA SUPERIOR, meteo perfecta]: "
                  f"{int((~hay_fc).sum()):,} filas con ERA5 de D+1 sin degradar")
        return datos

    # --- pseudo-prevision -----------------------------------------------------
    fechas = pd.to_datetime(datos["fecha_objetivo"]).dt.date
    fcs = pd.DataFrame({v: datos[f"{v}_fc"] for v in presentes})
    err = medir_error(fcs.loc[hay_fc], era5.loc[hay_fc, presentes], presentes)

    if verbose:
        print("  error de prevision medido en el solape (prevision D+1 vs ERA5 de D+1):")
        r = resumen_error(err, era5.loc[hay_fc])
        for _, x in r.iterrows():
            print(f"      {x['variable']:9s} sesgo {x['sesgo']:8.2f}  "
                  f"rmse {x['rmse']:9.2f}  rmse/sd {x['rmse_rel']:.2f}")

    destino = ~hay_fc
    pseudo, inf = pseudo_prevision(
        era5.loc[destino], err, fechas[hay_fc], datos.loc[hay_fc, "hora"],
        fechas[destino], datos.loc[destino, "hora"], presentes, verbose=verbose)

    for v in presentes:
        datos[f"{v}_meteo"] = datos[f"{v}_fc"]
        datos.loc[destino, f"{v}_meteo"] = pseudo[v].to_numpy()

    if verbose:
        print(f"  canal meteo [pseudo]: {len(presentes)} columnas · "
              f"{int(hay_fc.sum()):,} prevision real ({hay_fc.mean() * 100:.1f}%), "
              f"{int(destino.sum()):,} pseudo-prevision")
        for et in ("train", "validation", "test"):
            m = datos["split"] == et
            if m.any():
                print(f"      {et:11s} {datos.loc[m, 'meteo_es_forecast'].mean() * 100:5.1f}% "
                      f"con prevision real")
        # continuidad del empalme: la media de cada variable no debe dar un salto
        print("  continuidad en el empalme (media antes / despues de 2024-04):")
        corte = pd.Timestamp("2024-04-01")
        fo = pd.to_datetime(datos["fecha_objetivo"])
        for v in presentes[:4]:
            a = datos.loc[(fo < corte), f"{v}_meteo"].mean()
            b = datos.loc[(fo >= corte), f"{v}_meteo"].mean()
            print(f"      {v:9s} {a:10.2f}  {b:10.2f}   dif {b - a:+.2f}")
    return datos


def _capdisp_horario(datos: pd.DataFrame, verbose=True) -> pd.DataFrame:
    """Potencia disponible con su grano real: HORARIO, no la media del dia.

    El constructor del equipo entrega `capdisp_*` agregada a media diaria -- un valor por
    dia y por tecnologia -- pero `esios_capacity_available` es horaria desde que se corrigio
    el 23-ago-2026 (4,7 valores distintos por dia). Promediar tira el perfil intradiario de
    la hidraulica y del ciclo combinado, que son justo las tecnologias que marcan el
    marginal. Es el mismo defecto que se corrigio en el bloque PDBC.

    ALINEAMIENTO. Esta columna describe el DIA OBJETIVO (D+1), no el dia D, y eso esta
    verificado al 99,9 % contra la media diaria de la fuente. No es fuga: la potencia
    disponible es una declaracion previa de indisponibilidades que ESIOS publica por
    adelantado -- la tabla va un dia por delante de `esios_pbf_gen` y de `spot_price`. Por
    eso el merge va por `fecha_objetivo` y no por `fecha_pred`.
    """
    from config import load_config
    import psycopg2
    _, db = load_config()
    campos = ["hydro_mw", "pump_mw", "nuclear_mw", "coal_antracita_mw", "ccgt_mw", "fuel_mw"]
    con = psycopg2.connect(**db)
    try:
        g = pd.read_sql(f"SELECT datetime, {', '.join(campos)} "
                        f"FROM esios_capacity_available ORDER BY datetime", con)
    finally:
        con.close()

    loc = pd.to_datetime(g["datetime"], utc=True).dt.tz_convert(TZ)
    g["fecha_objetivo"] = pd.to_datetime(loc.dt.date)
    g["hora"] = loc.dt.hour
    # el retroceso horario de octubre repite la hora local 2
    g = g.sort_values("datetime").drop_duplicates(subset=["fecha_objetivo", "hora"],
                                                  keep="first")
    g = g.rename(columns={c: f"capdisp_{c}" for c in campos})

    viejas = [c for c in datos.columns if c.startswith("capdisp_")]
    datos = datos.drop(columns=viejas)
    n_antes = len(datos)
    datos = datos.merge(g[["fecha_objetivo", "hora"] + [f"capdisp_{c}" for c in campos]],
                        on=["fecha_objetivo", "hora"], how="left")
    assert len(datos) == n_antes, "el merge de capdisp no debe alterar las filas"

    if verbose:
        v = datos.groupby("fecha_objetivo")[[f"capdisp_{c}" for c in campos]].nunique().mean()
        print(f"  capdisp horario: {len(viejas)} columnas diarias -> {len(campos)} horarias "
              f"(mediana {v.median():.1f} valores distintos por dia)")
    return datos


def construir(cache: Path | str | None = CACHE, forzar: bool = False,
              verbose: bool = True) -> pd.DataFrame:
    """Dataset crudo desde Postgres, con el PBF y el canal meteo ya sustituidos."""
    cache = Path(cache) if cache else None
    if cache and cache.exists() and not forzar:
        if verbose:
            print(f"Leyendo de cache: {cache}  (usa forzar=True para reconstruir)")
        return pd.read_parquet(cache)

    import construir_dataset_maestro_sergio_v5 as v5
    from pdbc_horario import bloque_para_matriz as bloque_pdbc, cargar as cargar_pdbc

    _desactivar_filtros(v5, verbose)
    if verbose:
        print("\nLlamando al constructor del equipo (lee ~15 tablas, tarda unos minutos)...")
    datos = v5.construir_dataset_horario(
        incluir_clima=False,        # el clima como contexto diario no; el horario si entra
        incluir_contexto_diario=True,
        filtrar_cobertura=False,
        variante="sin_ntc_prev",    # las 6 NTC prev recortan la ventana y no aportan
    )
    if verbose:
        print(f"\nConstructor -> {datos.shape[0]:,} filas x {datos.shape[1]} columnas")

    # El constructor devuelve `fecha_pred` y `fecha_objetivo` como objetos `date`, y los
    # bloques propios los llevan en datetime64. Un merge entre esos dos dtypes es un
    # ValueError, asi que se normalizan aqui, una sola vez y antes de cualquier union.
    for col in ("fecha_pred", "fecha_objetivo"):
        if col in datos.columns:
            datos[col] = pd.to_datetime(datos[col])

    # `construir_dataset_horario` no escribe `split`: eso lo hace aparte
    # `dividir_train_val_test_horario`. Se anade aqui con las fronteras del equipo.
    #
    # El corte va por `fecha_pred` (el dia en que se emite la prediccion), no por
    # `fecha_objetivo`. Es lo que dice el protocolo -- "entrenad hasta el 31-dic-2024" se
    # refiere al dia en que se decide -- y coincide con el reparto del CSV del equipo:
    # la ultima fila de train tiene fecha_objetivo 2025-01-01, o sea fecha_pred 2024-12-31.
    fp = datos["fecha_pred"]
    datos["split"] = "test"
    datos.loc[fp <= pd.Timestamp(v5.VAL_END), "split"] = "validation"
    datos.loc[fp <= pd.Timestamp(v5.TRAIN_END), "split"] = "train"
    if verbose:
        print("  split por fecha_pred:", dict(datos["split"].value_counts()))

    # --- fuera el bloque PDBC -------------------------------------------------
    pdbc = [c for c in datos.columns if c.startswith("pdbc_")]
    if pdbc:
        datos = datos.drop(columns=pdbc)
        if verbose:
            print(f"  retiradas {len(pdbc)} columnas pdbc_*")

    # --- dentro el PDBC recalculado --------------------------------------------
    bloque = bloque_pdbc(cargar_pdbc(verbose=False), alineamiento="D")
    n_antes = len(datos)
    datos = datos.merge(bloque, on=["fecha_pred", "hora"], how="left")
    assert len(datos) == n_antes, "el merge del PDBC no debe alterar las filas"
    if verbose:
        nb = {p: sum(c.startswith(p) for c in bloque.columns)
              for p in ("pdbc_", "pbfli_", "bil_")}
        print(f"  incorporadas {bloque.shape[1] - 2} columnas: {nb}")

    # --- potencia disponible con grano horario ---------------------------------
    datos = _capdisp_horario(datos, verbose)

    # --- canal meteorologico --------------------------------------------------
    datos = _canal_meteo(datos, verbose=verbose)

    if verbose:
        print(f"\nDataset crudo: {datos.shape[0]:,} filas x {datos.shape[1]} columnas")
        print(f"  rango: {datos['fecha_objetivo'].min()} -> {datos['fecha_objetivo'].max()}")

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        datos.to_parquet(cache, index=False)
        if verbose:
            print(f"  guardado en {cache}")
    return datos


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--forzar", action="store_true", help="reconstruir aunque haya cache")
    a = ap.parse_args()
    construir(forzar=a.forzar)
