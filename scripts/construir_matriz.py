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

CANAL METEOROLOGICO: prevision desde 2024, ERA5 antes.
Un solo juego de columnas `*_meteo`, con este criterio:

    prevision ECMWF a D+1   donde existe  (2024-04 en adelante)
    ERA5 real desfasado     donde no      (2020 -> 2024-03)

La columna significa "la mejor estimacion del tiempo de D+1 disponible al predecir": antes
de 2024 eso era la persistencia del dia anterior, despues es la prevision numerica.

Y una bandera, `meteo_es_forecast`, que NO es decorativa. Las dos fuentes no miden el mismo
dia: ERA5 `_met_Dm1` es el tiempo real de D-1 y la prevision es la de D+1, dos dias de
separacion. Medido sobre las 20.661 horas de solape, el viento a 100 m correlaciona 0,44
entre ambas -- no es un fallo, es que el viento se decorrela muy rapido a esa escala (la
temperatura aguanta 0,94 por persistencia y la radiacion 0,85 por el ciclo astronomico).
Sin la bandera el modelo no puede distinguir los dos regimenes y el empalme seria una
trampa silenciosa. Las dos fuentes originales se conservan en el dataframe; el notebook
decide si las saca del pool.

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

# Pares (nombre corto, columna de ERA5) para el canal empalmado. El orden fija el de las
# columnas `*_meteo` resultantes.
PARES_METEO = [
    ("t2m", "t2m_mean_met_Dm1"), ("d2m", "d2m_mean_met_Dm1"),
    ("wind10", "wind10_mean_met_Dm1"), ("wind100", "wind100_mean_met_Dm1"),
    ("ssrd", "ssrd_mean_met_Dm1"), ("tcc", "tcc_mean_met_Dm1"),
    ("msl", "msl_mean_met_Dm1"),
]


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


def _canal_meteo(datos: pd.DataFrame, verbose=True) -> pd.DataFrame:
    """Empalma prevision y ERA5 en un solo canal `*_meteo`, con su bandera.

    Criterio: prevision donde la hay, ERA5 donde no. La columna pasa a significar "la
    mejor estimacion del tiempo de D+1 disponible al predecir".

    OJO al leerlo: las dos fuentes NO miden el mismo dia. ERA5 `_met_Dm1` es el tiempo
    real de D-1 y la prevision es la de D+1, dos dias de separacion. Medido sobre las
    20.661 horas de solape, el viento a 100 m correlaciona 0,44 entre las dos -- no es un
    fallo, es que el viento se decorrela muy rapido a esa escala. Por eso la bandera
    `meteo_es_forecast` no es decorativa: sin ella el modelo no puede distinguir los dos
    regimenes y el empalme seria una trampa silenciosa.
    """
    from ecmwf_horario import bloque_para_matriz as bloque_fc, cargar as cargar_fc

    fc = bloque_fc(cargar_fc(verbose=False))
    n_antes = len(datos)
    datos = datos.merge(fc, on=["fecha_objetivo", "hora"], how="left")
    assert len(datos) == n_antes, "el merge de la prevision no debe alterar las filas"

    mascara = datos["t2m_fc"].notna()
    datos["meteo_es_forecast"] = mascara.astype(int)

    creadas = []
    for corto, col_era5 in PARES_METEO:
        fc_col = f"{corto}_fc"
        if fc_col not in datos.columns or col_era5 not in datos.columns:
            if verbose:
                print(f"    aviso: falta {fc_col} o {col_era5}, se omite del canal")
            continue
        datos[f"{corto}_meteo"] = datos[fc_col].where(mascara, datos[col_era5])
        creadas.append(f"{corto}_meteo")

    if verbose:
        print(f"  canal meteo: {len(creadas)} columnas · "
              f"{int(mascara.sum()):,} filas de prevision ({mascara.mean() * 100:.1f}%), "
              f"{int((~mascara).sum()):,} de ERA5")
        for et in ("train", "validation", "test"):
            m = datos["split"] == et
            if m.any():
                print(f"      {et:11s} {datos.loc[m, 'meteo_es_forecast'].mean() * 100:5.1f}% "
                      f"con prevision")
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

    # --- canal meteorologico --------------------------------------------------
    datos = _canal_meteo(datos, verbose)

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
