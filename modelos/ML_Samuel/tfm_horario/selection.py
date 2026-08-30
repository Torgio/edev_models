"""Seleccion de features: filtro de Spearman + seleccion secuencial (SFS).

Equivale a las celdas 6, 10 y 13 del notebook. Ambos selectores se ajustan SOLO
sobre train -- la seleccion no debe ver filas de val/test (fuga de informacion).
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_selection import SequentialFeatureSelector
from sklearn.model_selection import TimeSeriesSplit

from . import ajustes as config

log = logging.getLogger(__name__)


def select_features_spearman(
    X: pd.DataFrame,
    y: pd.Series,
    target_threshold: float = config.SPEARMAN_TARGET_THRESHOLD,
    collinearity_threshold: float = config.SPEARMAN_COLLINEARITY_THRESHOLD,
    p_value_max: float = config.SPEARMAN_P_VALUE_MAX,
    min_overlap: int = config.SPEARMAN_MIN_OVERLAP,
    siempre_incluir: tuple = config.FEATURES_PROTEGIDAS,
) -> dict:
    """
    Etapa 1: se conservan las features cuya correlacion de Spearman con el target es
    a la vez relevante (|rho| >= target_threshold) y significativa (p < p_value_max).
    Etapa 2: entre las supervivientes, se descartan redundancias -- para cada par con
    |rho| >= collinearity_threshold se conserva la mas correlada con el target.

    `siempre_incluir`: features protegidas, nunca se descartan (ver config).

    Devuelve:
      selected          -- lista final, ordenada por |rho| con el target
      target_corr       -- rho con el target de cada feature numerica de entrada
      dropped_low_corr  -- cortadas en la etapa 1 (debiles o no significativas)
      dropped_collinear -- cortadas en la etapa 2, redundantes con otra mas fuerte
    """
    X_num = X.select_dtypes(include=[np.number])
    protegidas = [c for c in siempre_incluir if c in X_num.columns]

    rho, pval = {}, {}
    for col in X_num.columns:
        mask = X_num[col].notna() & y.notna()
        if mask.sum() < min_overlap:
            rho[col], pval[col] = np.nan, np.nan
            continue
        r, p = spearmanr(X_num.loc[mask, col], y[mask])
        rho[col], pval[col] = r, p

    target_corr = pd.Series(rho)
    pvals = pd.Series(pval)

    survivors = [
        c for c in X_num.columns
        if c in protegidas
        or (pd.notna(target_corr[c])
            and abs(target_corr[c]) >= target_threshold
            and pvals[c] < p_value_max)
    ]
    dropped_low_corr = [c for c in X_num.columns if c not in survivors]

    corr_matrix = X_num[survivors].corr(method="spearman").abs()
    por_rho = target_corr[survivors].abs().sort_values(ascending=False).index.tolist()
    # las protegidas van primero: asi pueden expulsar a un colineal, nunca al reves
    ordered = protegidas + [c for c in por_rho if c not in protegidas]

    selected, dropped_collinear, excluded = [], [], set()
    for col in ordered:
        if col in excluded:
            continue
        selected.append(col)
        for other in ordered:
            if other == col or other in excluded or other in selected:
                continue
            if other in protegidas:      # una protegida no se descarta por colinealidad
                continue
            if corr_matrix.loc[col, other] >= collinearity_threshold:
                excluded.add(other)
                dropped_collinear.append(other)

    log.info("Spearman: %d de %d features conservadas", len(selected), X_num.shape[1])
    log.info("protegidas presentes: %s", [c for c in siempre_incluir if c in selected])

    return {
        "selected": selected,
        "target_corr": target_corr.sort_values(key=abs, ascending=False),
        "dropped_low_corr": dropped_low_corr,
        "dropped_collinear": dropped_collinear,
    }


def select_features_sfs(
    X: pd.DataFrame,
    y: pd.Series,
    n_features_to_select: str | int = config.SFS_N_FEATURES,
    direction: str = config.SFS_DIRECTION,
    n_splits: int = config.SFS_N_SPLITS,
    scoring: str = config.SFS_SCORING,
    max_filas: int | None = config.SFS_MAX_FILAS,
    tol: float | None = config.SFS_TOL,
) -> dict:
    """
    Seleccion secuencial. Usa TimeSeriesSplit en vez de KFold: al ser una serie
    temporal, la CV nunca debe barajar las horas (evita entrenar con "futuro" y
    validar con "pasado").

    max_filas: SFS entrena O(n_features^2) x n_splits modelos y el dataset horario
    tiene ~24x mas filas que el diario. Por defecto la seleccion se hace sobre las
    ultimas `max_filas` horas de train (1 año, cubre el ciclo estacional completo).
    None = todo train, si hay computo de sobra.
    """
    if max_filas is not None and len(X) > max_filas:
        X, y = X.iloc[-max_filas:], y.iloc[-max_filas:]
        log.info("SFS sobre las ultimas %d horas de train (%s -> %s)", max_filas, X.index.min(), X.index.max())

    estimator = RandomForestRegressor(**config.SFS_RF_PARAMS)
    cv = TimeSeriesSplit(n_splits=n_splits)

    sfs = SequentialFeatureSelector(
        estimator,
        n_features_to_select=n_features_to_select,
        direction=direction,
        scoring=scoring,
        cv=cv,
        tol=tol,
        n_jobs=-1,      # el paralelismo va aqui, no en el bosque (ver SFS_RF_PARAMS)
    )
    t0 = time.time()
    sfs.fit(X, y)
    log.info("SFS ajustado en %.1f min", (time.time() - t0) / 60)

    selected = X.columns[sfs.get_support()].tolist()
    log.info("SFS: %d de %d features conservadas", len(selected), X.shape[1])

    return {"selected": selected, "estimator": estimator, "sfs": sfs}


# ---------------------------------------------------------------------------
# Diagnostico de colinealidad (celda 13)
# ---------------------------------------------------------------------------
def matriz_correlacion(X: pd.DataFrame, features: list | None = None, method: str = "spearman") -> pd.DataFrame:
    """Matriz de correlacion entre las features indicadas. Solo columnas numericas;
    las constantes se descartan porque rho no esta definido."""
    cols = features if features is not None else X.columns.tolist()
    X_num = X[cols].select_dtypes(include=[np.number])

    constantes = [c for c in X_num.columns if X_num[c].nunique(dropna=True) <= 1]
    if constantes:
        X_num = X_num.drop(columns=constantes)

    return X_num.corr(method=method)


def pares_mas_correlados(corr: pd.DataFrame, top_n: int = 15, umbral: float | None = None) -> pd.DataFrame:
    """Pares de features ordenados por |rho| descendente (triangulo superior, sin diagonal).
    Con `umbral`, devuelve solo los pares que lo superan."""
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    pares = (
        corr.where(mask)
        .stack()
        .rename("rho")
        .reset_index()
        .rename(columns={"level_0": "feature_a", "level_1": "feature_b"})
    )
    pares["abs_rho"] = pares["rho"].abs()
    pares = pares.sort_values("abs_rho", ascending=False)

    if umbral is not None:
        return pares[pares["abs_rho"] >= umbral].reset_index(drop=True)
    return pares.head(top_n).reset_index(drop=True)


def resumen_colinealidad(corr: pd.DataFrame, umbral: float = 0.90) -> dict:
    """|rho| medio y numero de pares por encima del umbral usado en la etapa 2."""
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    valores = corr.where(mask).stack().abs()
    return {
        "rho_medio": float(valores.mean()),
        "pares_sobre_umbral": int((valores >= umbral).sum()),
        "umbral": umbral,
    }
