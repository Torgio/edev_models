"""Metricas y tablas de comparacion (celdas 55 y 59 del notebook)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from . import ajustes as config

log = logging.getLogger(__name__)

NOMBRE_NAIVE_LOCAL = config.NOMBRE_NAIVE


def naive_lag24(y_train: pd.Series, y_objetivo: pd.Series) -> pd.Series:
    """Baseline naive horario: precio de la MISMA hora del dia anterior (t-24).

    El t-1 del dataset diario era "el precio de ayer"; su equivalente aqui son 24
    posiciones atras. Se toma de train + objetivo concatenados para que las 24
    primeras horas del split evaluado tampoco necesiten bfill (que ademas miraria
    hacia el futuro).

    Esta en evaluation y no en models/ porque no es un modelo que se entrene: es
    la referencia contra la que se mide todo (el denominador del rMAE).
    """
    serie_completa = pd.concat([y_train, y_objetivo]).sort_index()
    naive_pred = serie_completa.shift(24).reindex(y_objetivo.index)
    log.info("NaN en naive_pred: %d", int(naive_pred.isna().sum()))
    return naive_pred


def calcular_metricas(
    y_true: pd.Series,
    predictions_dict: dict[str, pd.Series],
    nombre_naive: str = config.NOMBRE_NAIVE,
) -> pd.DataFrame:
    """MAE, RMSE y rMAE (MAE relativo al baseline naive) por modelo."""
    if nombre_naive not in predictions_dict:
        raise KeyError(f"Falta el baseline {nombre_naive!r} en predictions_dict para calcular rMAE")

    mae_naive = mean_absolute_error(y_true, predictions_dict[nombre_naive].reindex(y_true.index))

    filas = []
    for nombre, pred in predictions_dict.items():
        pred_alineado = pred.reindex(y_true.index)
        mae = mean_absolute_error(y_true, pred_alineado)
        rmse = np.sqrt(mean_squared_error(y_true, pred_alineado))
        filas.append({"modelo": nombre, "MAE": mae, "RMSE": rmse, "rMAE": mae / mae_naive})

    return pd.DataFrame(filas).set_index("modelo").sort_values("MAE")


def tabla_predicciones(y_true: pd.Series, predictions_dict: dict[str, pd.Series]) -> pd.DataFrame:
    """Real + prediccion de cada modelo + error absoluto hora a hora."""
    tabla = pd.DataFrame({"Real": y_true})
    for nombre, pred in predictions_dict.items():
        pred_alineado = pred.reindex(y_true.index)
        tabla[nombre] = pred_alineado
        tabla[f"MAE {nombre}"] = (y_true - pred_alineado).abs()   # == MAE de esa hora, n=1
    return tabla.round(2)


def informe_modelo(
    nombre: str,
    pred: pd.Series,
    y_train: pd.Series,
    y_val: pd.Series,
) -> pd.DataFrame:
    """Metricas de UN modelo frente al baseline naive, para cuando se ejecuta suelto.

    Cada script de `models/` termina llamando aqui: asi una ejecucion individual ya
    dice si el modelo bate al naive (rMAE < 1) sin tener que lanzar despues la
    comparativa completa.
    """
    metricas = calcular_metricas(y_val, {NOMBRE_NAIVE_LOCAL: naive_lag24(y_train, y_val), nombre: pred})
    log.info("\n%s", metricas.to_string())

    config.preparar_entorno()
    destino = config.METRICS_DIR / f"metricas_{nombre.lower().replace(' ', '_')}.csv"
    metricas.to_csv(destino)
    log.info("metricas guardadas -> %s", destino)
    return metricas


def guardar_resultados(
    y_true: pd.Series,
    predictions_dict: dict[str, pd.Series],
    etiqueta: str = "validation",
) -> dict[str, Path]:
    """Vuelca metricas, predicciones y tabla detallada a disco."""
    config.preparar_entorno()

    metricas = calcular_metricas(y_true, predictions_dict)
    preds = pd.DataFrame({n: p.reindex(y_true.index) for n, p in predictions_dict.items()})
    preds.insert(0, "Real", y_true)

    rutas = {
        "metricas": config.METRICS_DIR / f"metricas_{etiqueta}.csv",
        "predicciones": config.PREDICTIONS_DIR / f"predicciones_{etiqueta}.csv",
        "detalle": config.PREDICTIONS_DIR / f"detalle_{etiqueta}.csv",
    }
    metricas.to_csv(rutas["metricas"])
    preds.to_csv(rutas["predicciones"])
    tabla_predicciones(y_true, predictions_dict).to_csv(rutas["detalle"])

    log.info("Metricas (%s):\n%s", etiqueta, metricas.to_string())
    for k, v in rutas.items():
        log.info("guardado %s -> %s", k, v)
    return rutas
