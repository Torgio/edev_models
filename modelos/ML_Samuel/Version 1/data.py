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

import numpy as np
import pandas as pd

from . import ajustes as config

log = logging.getLogger(__name__)


# ===========================================================================
# 1. CARGA Y SPLIT
# ===========================================================================
def cargar_dataset(solo_filas_validas: bool | None = None, pdbc: str | None = None) -> pd.DataFrame:
    """Construye el dataset horario desde la BBDD.

    El import va dentro de la funcion a proposito: `preparar_entorno()` tiene que
    haber metido MODELOS_DIR en sys.path antes.
    """
    config.preparar_entorno()
    from construir_dataset_horario import construir_dataset_horario

    dataset = construir_dataset_horario(
        solo_filas_validas=config.SOLO_FILAS_VALIDAS if solo_filas_validas is None else solo_filas_validas,
        pdbc=config.PDBC if pdbc is None else pdbc,
    )
    log.info("dataset: %d filas (horas) x %d columnas", dataset.shape[0], dataset.shape[1])
    return dataset


def columnas_con_fuga(columnas) -> list[str]:
    """Columnas que Prod.txt prohibe: se refieren al dia objetivo y su fuente publica
    despues de las 11:00 de la vispera.

    Son dos grupos (el porque, en el bloque FILTRO DE FUGA de ajustes.py):
      - ERA5 sin desfase: reanalisis del propio dia objetivo. Sus *_lag24h/_lag168h si valen.
      - `PELIGRO_mismodia_*`: PDBC del propio D+1, resultado de la misma subasta.

    La lista de columnas de clima se lee de `construir_dataset_maestro`, que es la
    fuente de verdad; si no se puede importar, se usa el fallback de ajustes.py.
    """
    try:
        config.preparar_entorno()
        from construir_dataset_maestro import COLS_CLIMA
        clima = tuple(COLS_CLIMA)
    except Exception:                                   # noqa: BLE001
        clima = config.CLIMA_FALLBACK
        log.warning("no se pudo importar COLS_CLIMA; se usa CLIMA_FALLBACK (%d columnas)", len(clima))

    prohibidas = []
    for c in columnas:
        if c.startswith(config.PREFIJOS_PROHIBIDOS):
            prohibidas.append(c)
        elif c in clima and not c.endswith(config.SUFIJOS_DE_LAG):
            prohibidas.append(c)                        # ERA5 del dia objetivo
    return prohibidas


def filtrar_fuga(X: pd.DataFrame) -> pd.DataFrame:
    """Aplica `columnas_con_fuga` y deja constancia en el log de que se ha quitado."""
    fuera = columnas_con_fuga(X.columns)
    if fuera:
        log.warning("FILTRO DE FUGA: se descartan %d columnas -> %s", len(fuera), fuera)
    else:
        log.info("FILTRO DE FUGA: ninguna columna prohibida en el dataset")
    return X.drop(columns=fuera)


def features_dudosas(columnas) -> list[str]:
    """Features que un revisor deberia mirar dos veces, para `metadata.json`.

    Criterio: las que describen la hora objetivo SIN desfase y no son de calendario.
    Son legitimas segun el constructor (previsiones day-ahead publicadas antes del
    cierre, commodities de D-1), pero es justo la categoria sobre la que avisa
    Prod.txt, asi que se declaran en vez de esconderlas.
    """
    calendario = {"hora", "dow", "month", "is_weekend"}
    return sorted(
        c for c in columnas
        if c not in calendario and not c.endswith(config.SUFIJOS_DE_LAG)
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
    """Separa el target del resto de features.

    En el dataset horario la fila YA ES la hora objetivo, asi que no hay columnas
    price_h00..price_h23 que descartar: solo hay que sacar `precio`.

    OJO: las columnas *_lag24h / *_lag168h (incluida precio_propio_lag24h) NO son fuga
    -- son datos que ya habian ocurrido cuando se cerro el mercado del dia anterior.
    Y `hora` es una feature mas, no una columna distinta por hora.
    """
    if target not in df.columns:
        raise ValueError(f"{target!r} no esta en el dataset: {list(df.columns)[:10]}...")
    y = df[target].rename(target)
    X = df.drop(columns=[target])
    return X, y


def cargar_splits(incluir_test: bool = False):
    """Devuelve un dict con X/y de train y validation (y de test solo si se pide).

    El test esta SELLADO hasta la fecha de apertura: pedirlo antes revienta a
    proposito, para que no se abra por accidente desde un cron.
    """
    dataset = cargar_dataset()
    train_raw, val_raw, test_raw = dividir_train_val_test_tfm(dataset)

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
# En el notebook `aplicar_tratamiento` era una funcion que cerraba sobre variables
# globales (selected_sfs, cols_a_dropear, cols_imputar, cols_con_missing) calculadas
# a mano mas arriba. Aqui es un objeto `TratamientoHorario` que se AJUSTA con train
# y guarda esa receta dentro, asi que:
#   - se puede serializar y reusar el dia que se abra el test,
#   - es imposible que val/test reciban un preprocesado distinto al de train,
#   - no hay estado global que se pise entre ejecuciones.
# ===========================================================================
def rellenar_gaps(df_or_series, limit: int = config.GAP_LIMIT_HORAS, freq: str = config.FREQ):
    """Rellena huecos cortos con interpolate sobre una rejilla HORARIA regular.

    `limit` se cuenta en horas: limit=6 rellena huecos de hasta 6h; un hueco mas
    largo se deja como NaN a proposito, para que se vea (era un dia entero de datos
    perdidos, no un parpadeo).
    """
    full_idx = pd.date_range(df_or_series.index.min(), df_or_series.index.max(), freq=freq)
    out = df_or_series.reindex(full_idx)
    out = out.interpolate(limit=limit)
    out.index.freq = freq
    return out


def normalizar_indice(obj):
    """Quita la tz del indice.

    El dataset horario viene en UTC. statsmodels/pmdarima quieren un DatetimeIndex
    regular con freq: sobre UTC (sin cambios de hora) quitar la tz NO altera el
    espaciado y evita los warnings de "no supported index". Si convirtieramos a
    Europe/Madrid antes de quitar la tz, los dias de DST tendrian 23 o 25 horas y
    el freq='h' se romperia.
    """
    obj = obj.copy()
    idx = pd.DatetimeIndex(obj.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    obj.index = idx
    return obj


def preparar_target(y: pd.Series) -> pd.Series:
    """Mismo tratamiento de indice y huecos que reciben las features."""
    y = rellenar_gaps(y.sort_index())
    y = normalizar_indice(y)
    return rellenar_gaps(y.sort_index())


def _cola_nan(s: pd.Series) -> int:
    return int(s.isna()[::-1].cumprod().sum())


def _cabeza_nan(s: pd.Series) -> int:
    return int(s.isna().cumprod().sum())


class TratamientoHorario:
    """Receta de tratamiento ajustada SOLO con train y aplicada igual a val y test.

    fit(X_train, target_corr) calcula:
      cols_a_dropear     -- columnas con mucho missing que se eliminan
      cols_imputar       -- columnas con mucho missing que se conservan e imputan
      cols_con_missing   -- resto de columnas con algun NaN residual
      columnas_finales   -- orden de columnas de salida
    """

    def __init__(
        self,
        selected: list[str],
        missing_pct_alto: float = config.MISSING_PCT_ALTO,
        umbral_corr: float = config.UMBRAL_CORR_DROP,
    ):
        self.selected = list(selected)
        self.missing_pct_alto = missing_pct_alto
        self.umbral_corr = umbral_corr
        self.cols_a_dropear: list[str] = []
        self.cols_imputar: list[str] = []
        self.cols_con_missing: list[str] = []
        self.columnas_finales: list[str] = []
        self.diagnostico_missing: pd.DataFrame | None = None
        self._ajustado = False

    # -- ajuste ------------------------------------------------------------
    def fit(self, X_raw: pd.DataFrame, target_corr: pd.Series) -> "TratamientoHorario":
        X = rellenar_gaps(X_raw[self.selected].copy())

        missing_pct = X.isna().mean().sort_values(ascending=False)
        high_missing = missing_pct[missing_pct > self.missing_pct_alto]
        corr_abs = target_corr.abs()

        self.diagnostico_missing = pd.DataFrame({
            "missing_pct": high_missing,
            "target_corr": target_corr.reindex(high_missing.index),
        })

        # ------------------------------------------------------------------
        # OJO / REVISAR: esta condicion es la MISMA que en el notebook (celda 18),
        # que dropea cuando |rho| > umbral. El comentario original decia
        # "dropeamos las que tienen mucho missing Y correlacion DEBIL", que seria
        # `< umbral`. Se mantiene el comportamiento del notebook para no cambiar
        # resultados sin querer; si la intencion era la del comentario, invierte
        # el operador aqui y vuelve a lanzar el pipeline con --forzar.
        # ------------------------------------------------------------------
        self.cols_a_dropear = [
            c for c in high_missing.index
            if corr_abs.get(c, 0) > self.umbral_corr and c in X.columns
        ]
        X = X.drop(columns=self.cols_a_dropear)
        log.info("se dropean (%d): %s", len(self.cols_a_dropear), self.cols_a_dropear)

        # Las que tienen mucho missing pero sobreviven al drop se imputan
        self.cols_imputar = [c for c in high_missing.index if c in X.columns]
        for col in self.cols_imputar:
            log.info(
                "%s: train %dh al inicio / %dh al final",
                col, _cabeza_nan(X[col]), _cola_nan(X[col]),
            )
            X[col] = X[col].bfill().ffill()

        # Resto de NaN residuales (interpolate no puede rellenar los iniciales)
        self.cols_con_missing = X.columns[X.isna().any()].tolist()
        for col in self.cols_con_missing:
            X[col] = X[col].bfill().ffill()

        self.columnas_finales = X.columns.tolist()
        self._ajustado = True

        log.info("NaN restantes en train: %d", int(X.isna().sum().sum()))
        log.info("Inf en train: %d", int(np.isinf(X.select_dtypes(include=[np.number])).sum().sum()))
        return self

    # -- aplicacion --------------------------------------------------------
    def transform(self, X_raw: pd.DataFrame) -> pd.DataFrame:
        if not self._ajustado:
            raise RuntimeError("Llama a fit() con train antes de transform().")

        X = X_raw[self.selected].copy()
        X = rellenar_gaps(X)
        X = X.drop(columns=[c for c in self.cols_a_dropear if c in X.columns])
        for col in self.cols_imputar + self.cols_con_missing:
            if col in X.columns:
                X[col] = X[col].bfill().ffill()

        X = X[self.columnas_finales]
        X = normalizar_indice(X)
        X = rellenar_gaps(X.sort_index())

        restantes = int(X.isna().sum().sum())
        if restantes:
            log.warning("Quedan %d NaN tras el tratamiento", restantes)
        return X

    def fit_transform(self, X_raw: pd.DataFrame, target_corr: pd.Series) -> pd.DataFrame:
        self.fit(X_raw, target_corr)
        return self.transform(X_raw)


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
