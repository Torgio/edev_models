"""Datos: carga, split y tratamiento.

Une lo que antes eran `data.py` y `preprocessing.py`. Cubre todo el recorrido de
una fila desde la BBDD hasta que entra en un modelo:

    BBDD -> construir_dataset_horario -> split train/val/test -> X, y
         -> huecos, drop, imputacion, indice sin tz -> listo para modelar

Equivale a las celdas 3, 4 y 16-24 del notebook.

Las dos mitades estan separadas por un banner mas abajo. La primera es la unica
que toca la base de datos, y lo hace con el import dentro de la funcion: importar
este modulo no exige tener conexion, asi que el tratamiento se puede probar en
seco (tests, portatil sin VPN) sin arrastrar psycopg2.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import ajustes as config

log = logging.getLogger(__name__)


# ===========================================================================
# 1. CARGA Y SPLIT
# ===========================================================================
def cargar_dataset(ruta: Path | str | None = None) -> pd.DataFrame:
    """Lee `matriz_nucleo.csv`, la matriz depurada del equipo.

    Sustituye a la carga desde Postgres: ya no se construye el dataset aqui, viene
    hecho de `construir_matriz.py` + `depurar_matriz.py` + `auditoria_frontera.py`.
    Este modulo solo lo valida, lo indexa por la hora objetivo y lo parte.

    El indice es `ts` en UTC (la hora objetivo). Se comprueban las tres cosas que
    Nucleo.txt promete -- 133 columnas, 0 nulos y rejilla horaria continua -- porque
    si alguna deja de cumplirse conviene enterarse ANTES de entrenar, no al final.
    """
    config.preparar_entorno()
    ruta = Path(ruta) if ruta is not None else config.RUTA_MATRIZ
    if not ruta.exists():
        raise FileNotFoundError(
            f"No encuentro matriz_nucleo.csv en {ruta}.\n"
            "Comprueba la ruta o exporta TFM_RUTA_MATRIZ=/ruta/a/matriz_nucleo.csv"
        )

    dataset = pd.read_csv(ruta)
    log.info("matriz leida de %s: %d filas x %d columnas", ruta, *dataset.shape)

    if dataset.shape[1] != config.COLUMNAS_ESPERADAS:
        log.warning("la matriz tiene %d columnas y se esperaban %d: puede haber cambiado",
                    dataset.shape[1], config.COLUMNAS_ESPERADAS)

    if config.COL_TIMESTAMP not in dataset.columns:
        raise ValueError(f"la matriz no trae la columna {config.COL_TIMESTAMP!r}")

    ts = pd.to_datetime(dataset[config.COL_TIMESTAMP], utc=True)
    dataset = dataset.set_index(pd.DatetimeIndex(ts, name="ts")).sort_index()

    duplicadas = dataset.index[dataset.index.duplicated()]
    if len(duplicadas):
        log.warning("%d timestamps duplicados (se conserva el primero): %s",
                    len(duplicadas), list(duplicadas[:5]))
        dataset = dataset[~dataset.index.duplicated(keep="first")]

    nulos = int(dataset.isna().sum().sum())
    log.info("nulos en la matriz: %d %s", nulos, "(como promete Nucleo.txt)" if nulos == 0 else "(!)")

    dataset = _completar_rejilla(dataset)
    log.info("rango: %s -> %s (%d horas)", dataset.index.min(), dataset.index.max(), len(dataset))
    return dataset


def _completar_rejilla(dataset: pd.DataFrame) -> pd.DataFrame:
    """Rellena las horas UTC que falten para que el indice sea una rejilla continua.

    Hace falta por dos motivos:

    1. statsmodels necesita un DatetimeIndex con `freq`, y pandas se niega a
       ponerlo si hay un solo hueco ("Inferred frequency None does not conform").
    2. `pred_val_2025.csv` debe tener 8760 filas exactas.

    Por que faltan horas: la matriz parece construirse por dia LOCAL x 24 slots, y
    eso no cubre las 8760 horas UTC del año. En el cambio de hora de marzo el dia
    local tiene 23 horas y en el de octubre 25, asi que la correspondencia
    "24 slots por dia" se descuadra justo en esas dos fechas. De ahi que validation
    saliera con 8759 filas en vez de 8760.

    Se rellenan solo huecos PEQUEÑOS. Si faltan muchas horas no es el cambio de
    hora, es que la matriz esta incompleta, y entonces el pipeline para: rellenar
    cientos de horas por interpolacion falsearia el entrenamiento en silencio.
    """
    completa = pd.date_range(dataset.index.min(), dataset.index.max(), freq=config.FREQ, tz="UTC")
    faltan = completa.difference(dataset.index)
    if not len(faltan):
        return dataset

    if len(faltan) > config.MAX_HORAS_A_RELLENAR:
        raise ValueError(
            f"faltan {len(faltan)} horas en la matriz (mas de {config.MAX_HORAS_A_RELLENAR}). "
            f"Primeras: {list(faltan[:5])}. No se rellenan: revisa construir_matriz.py "
            "o sube MAX_HORAS_A_RELLENAR en ajustes.py si sabes que es correcto."
        )

    log.warning("faltan %d horas en la rejilla UTC; se interpolan. Son: %s",
                len(faltan), [str(t) for t in faltan])

    dataset = dataset.reindex(completa)
    numericas = dataset.select_dtypes(include=[np.number]).columns
    dataset[numericas] = dataset[numericas].interpolate(limit_direction="both")
    otras = [c for c in dataset.columns if c not in numericas]
    if otras:
        dataset[otras] = dataset[otras].ffill().bfill()
    dataset.index.name = "ts"
    return dataset


def columnas_con_fuga(columnas) -> list[str]:
    """Columnas prohibidas por la frontera de informacion de Prod.txt.

    Por defecto, ninguna: la matriz ya paso `auditoria_frontera.py` y en su
    convencion el sufijo `_D` es el dia en que se predice, no el dia objetivo (el
    razonamiento completo esta en el bloque FRONTERA DE INFORMACION de ajustes.py).
    Si esa lectura resultara equivocada, se rellenan `COLUMNAS_PROHIBIDAS` /
    `PREFIJOS_PROHIBIDOS` en ajustes.py y este filtro las quita sin tocar nada mas.
    """
    return sorted({
        c for c in columnas
        if c in config.COLUMNAS_PROHIBIDAS
        or (config.PREFIJOS_PROHIBIDOS and c.startswith(config.PREFIJOS_PROHIBIDOS))
    })


def filtrar_fuga(X: pd.DataFrame) -> pd.DataFrame:
    """Aplica `columnas_con_fuga` y deja constancia en el log."""
    fuera = columnas_con_fuga(X.columns)
    if fuera:
        log.warning("FRONTERA: se descartan %d columnas -> %s", len(fuera), fuera)
    else:
        log.info("FRONTERA: sin columnas prohibidas "
                 "(la matriz ya paso auditoria_frontera.py; ver ajustes.py)")
    return X.drop(columns=fuera)


def features_dudosas(columnas) -> list[str]:
    """Las que se declaran en metadata.json para que el revisor las mire.

    No se descartan. Son las `*_meteo` (prevision del dia objetivo, real solo desde
    2024-04 segun Nucleo.txt) y los testigos de publicacion del PBF. El detalle,
    en ajustes.py.
    """
    return sorted(
        c for c in columnas
        if c.endswith(config.SUFIJOS_DUDOSOS) or c in config.DUDOSAS_EXPLICITAS
    )


def dividir_train_val_test_tfm(dataset: pd.DataFrame):
    """Split por TIMESTAMP UTC (no por dia local Madrid).

    Lo exige el formato de entrega: `pred_val_2025.csv` son las 8760 horas de 2025
    en UTC. Con el criterio de dia local, las 23:00 UTC del 31-dic caerian en el
    split siguiente y el CSV saldria con 8759 u 8761 filas.

    El precio de perderlo: un dia local ya no cae entero en el mismo split (las dos
    ultimas horas del 31-dic-2024 local son 2025 en UTC). Solo afecta a la frontera
    entre splits, y el formato manda.
    """
    idx = dataset.index
    val_ini = pd.Timestamp(config.VAL_INICIO_UTC)
    val_fin = pd.Timestamp(config.VAL_FIN_UTC)
    test_ini = pd.Timestamp(config.TEST_INICIO_UTC)

    return (
        dataset[idx < val_ini],
        dataset[(idx >= val_ini) & (idx <= val_fin)],
        dataset[idx >= test_ini],
    )


def rejilla_val_2025() -> pd.DatetimeIndex:
    """Las 8760 horas UTC de 2025, que es el indice exacto de pred_val_2025.csv."""
    rejilla = pd.date_range(config.VAL_INICIO_UTC, config.VAL_FIN_UTC, freq=config.FREQ, tz="UTC")
    if len(rejilla) != config.HORAS_VAL_2025:
        raise RuntimeError(f"la rejilla de 2025 tiene {len(rejilla)} horas, se esperaban {config.HORAS_VAL_2025}")
    return rejilla


def preparar_xy(df: pd.DataFrame, target: str = config.TARGET):
    """Separa el objetivo de las features y descarta las columnas de control.

    `fecha_pred`, `fecha_objetivo`, `ts` y `split` identifican la fila; meterlas
    como features seria darle al modelo la fecha como predictor. `hora` SI se queda:
    es control y feature a la vez, y es la variable que mas explica el perfil
    horario del precio.
    """
    if target not in df.columns:
        raise ValueError(f"{target!r} no esta en la matriz. Columnas: {list(df.columns)[:8]}...")

    y = df[target].rename(target)
    sobran = [c for c in (*config.COLUMNAS_CONTROL, target) if c in df.columns]
    X = df.drop(columns=sobran)
    log.info("features: %d (se apartan %s)", X.shape[1], sobran)
    return X, y


def comprobar_split_de_la_matriz(dataset: pd.DataFrame, train, val) -> None:
    """La matriz trae su propia columna `split`. Se compara con el corte de Prod.txt.

    No se usa la suya: las fronteras las fija Prod.txt (train <= 2024, validation
    2025 en UTC) y el CSV de entrega exige 8760 filas exactas. Pero si discrepan,
    conviene saberlo antes de entrenar.
    """
    if "split" not in dataset.columns:
        return
    valores = dataset["split"].astype(str)
    for nombre, trozo in (("train", train), ("validation", val)):
        if not len(trozo):
            continue
        reparto = valores.reindex(trozo.index).value_counts().to_dict()
        log.info("%s segun la columna `split` de la matriz: %s", nombre, reparto)
        if len(reparto) > 1:
            log.warning("el corte de Prod.txt no coincide con la columna `split` en %s: "
                        "manda Prod.txt, pero revisalo", nombre)


def cargar_splits(incluir_test: bool = False):
    """Devuelve un dict con X/y de train y validation (y de test solo si se pide).

    El test esta SELLADO hasta la fecha de apertura: pedirlo antes revienta a
    proposito, para que no se abra por accidente desde un cron.
    """
    dataset = cargar_dataset()
    train_raw, val_raw, test_raw = dividir_train_val_test_tfm(dataset)
    comprobar_split_de_la_matriz(dataset, train_raw, val_raw)

    for nombre, split in (("train", train_raw), ("val", val_raw), ("test", test_raw)):
        if len(split):
            log.info("%-5s: %6d horas  (%s -> %s)", nombre, len(split), split.index.min(), split.index.max())

    X_train, y_train = preparar_xy(train_raw)
    X_val, y_val = preparar_xy(val_raw)

    # El filtro de fuga se aplica ANTES de la seleccion: una columna prohibida no
    # debe ni competir por entrar en el modelo.
    X_train = filtrar_fuga(X_train)
    X_val = X_val[X_train.columns]

    faltan = len(rejilla_val_2025().difference(X_val.index))
    if faltan:
        log.warning("a validation le faltan %d de las 8760 horas UTC de 2025 "
                    "(se rellenaran al construir el entregable)", faltan)

    splits = {"X_train": X_train, "y_train": y_train, "X_val": X_val, "y_val": y_val}

    if incluir_test:
        hoy = date.today()
        if hoy < config.FECHA_APERTURA_TEST:
            raise RuntimeError(
                f"Test SELLADO hasta {config.FECHA_APERTURA_TEST} (hoy es {hoy}). "
                "Una sola apertura: no se abre desde un pipeline automatico."
            )
        X_test, y_test = preparar_xy(test_raw)
        splits["X_test"], splits["y_test"] = X_test, y_test

    return splits


# ===========================================================================
# 2. TRATAMIENTO
# ===========================================================================
# Aqui vivia TratamientoHorario: interpolacion de huecos, deteccion de columnas con
# mucho missing, drop, imputacion bfill/ffill... Todo eso sobra desde que la entrada
# es matriz_nucleo.csv, que llega con 0 nulos y el apagon de abril-2025 ya imputado y
# marcado (`imputado_apagon`, `ventana_pisa_apagon`) por `depurar_matriz.py`.
#
# Lo unico que queda es lo que los modelos SI necesitan: quedarse con las features
# elegidas, dejar el indice como lo quiere statsmodels, y comprobar que la promesa
# de "0 nulos" se sigue cumpliendo en vez de darla por buena.
# ===========================================================================
def normalizar_indice(obj):
    """Quita la tz del indice.

    La matriz viene en UTC. statsmodels/pmdarima quieren un DatetimeIndex regular
    con freq: sobre UTC (sin cambios de hora) quitar la tz NO altera el espaciado y
    evita los warnings de "no supported index". Si convirtieramos a Europe/Madrid
    antes de quitar la tz, los dias de DST tendrian 23 o 25 horas y freq='h' se
    romperia.
    """
    obj = obj.copy()
    idx = pd.DatetimeIndex(obj.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    obj.index = idx

    # freq solo si el indice ES realmente regular. Asignarlo a ciegas revienta con
    # "Inferred frequency None does not conform to passed frequency h" en cuanto
    # falta una hora, y el mensaje no dice cual falta. La rejilla se completa antes,
    # en cargar_dataset(); aqui solo se etiqueta lo que ya deberia estar bien.
    try:
        obj.index.freq = config.FREQ
    except ValueError:
        log.warning("el indice no es una rejilla horaria continua: se deja sin freq. "
                    "statsmodels puede quejarse; revisa el aviso de huecos de cargar_dataset()")
    return obj


def verificar_sin_nulos(obj, etiqueta: str) -> None:
    """La matriz promete 0 nulos. Si deja de cumplirse hay que enterarse aqui.

    No se imputa nada a proposito: un NaN en esta matriz significa que algo ha
    cambiado aguas arriba (`construir_matriz.py` / `depurar_matriz.py`), y taparlo
    con un ffill esconderia el problema en vez de resolverlo.
    """
    nulos = int(np.asarray(obj.isna()).sum())
    if nulos:
        columnas = (obj.columns[obj.isna().any()].tolist()
                    if isinstance(obj, pd.DataFrame) else [getattr(obj, "name", etiqueta)])
        raise ValueError(
            f"{etiqueta}: {nulos} nulos en una matriz que deberia traer 0. "
            f"Columnas afectadas: {columnas[:10]}. Revisa depurar_matriz.py antes de entrenar."
        )


def aplicar_features(X: pd.DataFrame, features: list[str], etiqueta: str = "X") -> pd.DataFrame:
    """Deja X con las features elegidas, en el mismo orden, y el indice normalizado.

    Es lo que garantiza que validation reciba exactamente el mismo tratamiento que
    train: mismas columnas y mismo orden, que es de lo que se encargaba la receta
    de TratamientoHorario.
    """
    faltan = [c for c in features if c not in X.columns]
    if faltan:
        raise ValueError(f"{etiqueta}: faltan features seleccionadas -> {faltan[:10]}")

    X = normalizar_indice(X[list(features)])
    verificar_sin_nulos(X, etiqueta)
    return X


def preparar_target(y: pd.Series) -> pd.Series:
    """Mismo tratamiento de indice que reciben las features."""
    y = normalizar_indice(y.sort_index())
    verificar_sin_nulos(y, "target")
    return y


def escalar(X_train: pd.DataFrame, *otros: pd.DataFrame):
    """StandardScaler ajustado SOLO con train y aplicado al resto de splits.

    Vive aqui y no en `models/` porque es tratamiento de datos y lo comparten
    Ridge y ElasticNet: si cada modelo ajustase su propio scaler harian el mismo
    trabajo dos veces, y bastaria un despiste para que uno de los dos lo ajustase
    sobre validation (fuga).

    Devuelve (scaler, X_train_escalado, *resto_escalados).
    """
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_train_scaled = pd.DataFrame(
        scaler.fit_transform(X_train), index=X_train.index, columns=X_train.columns
    )
    escalados = [
        pd.DataFrame(scaler.transform(X), index=X.index, columns=X.columns) for X in otros
    ]
    return scaler, X_train_scaled, *escalados
