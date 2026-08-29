"""Preparacion de datos comun a los cuatro modelos.

Encadena las tres etapas previas a cualquier entrenamiento:

    carga + split  ->  seleccion (Spearman + SFS)  ->  tratamiento  ->  (escalado)

Antes esto vivia dentro de `run_pipeline.py`, asi que un modelo solo se podia
entrenar a traves del orquestador. Al moverlo aqui, cada script de `models/` lo
llama por su cuenta y se ejecuta solo:

    python -m tfm_horario.models.elasticnet

Cada paso se cachea en disco (ver `artifacts.py`), de modo que el primer modelo
que se lance paga el SFS y los tres siguientes lo reutilizan en segundos.
"""

from __future__ import annotations

import logging

from . import ajustes as config, artifacts, data, selection

log = logging.getLogger(__name__)


def preparar_splits(forzar: bool = False) -> dict:
    """Carga el dataset de la BBDD y lo parte en train / validation."""
    splits = artifacts.cachear("splits", lambda: data.cargar_splits(), forzar)
    log.info("train %s | val %s", splits["X_train"].shape, splits["X_val"].shape)
    return splits


def preparar_seleccion(forzar: bool = False):
    """Spearman + SFS sobre train. Es la etapa cara del pipeline (el SFS entrena
    O(n_features^2) x n_splits random forests), por eso se cachea con especial
    interes: solo se recalcula con --forzar."""
    splits = preparar_splits()
    X_train, y_train = splits["X_train"], splits["y_train"]

    result = artifacts.cachear(
        "spearman",
        lambda: selection.select_features_spearman(X_train, y_train),
        forzar,
    )
    X_train_sel = X_train[result["selected"]]

    result_sfs = artifacts.cachear(
        "sfs",
        lambda: selection.select_features_sfs(X_train_sel, y_train),
        forzar,
    )
    selected_sfs = result_sfs["selected"]

    # Diagnostico de colinealidad por log (antes eran dos heatmaps)
    for etiqueta, cols in (("Spearman", result["selected"]), ("Spearman+SFS", selected_sfs)):
        corr = selection.matriz_correlacion(X_train, cols)
        log.info("%s -> %d features | %s", etiqueta, len(cols), selection.resumen_colinealidad(corr))
        log.info("pares mas correlados tras %s:\n%s", etiqueta,
                 selection.pares_mas_correlados(corr, top_n=10).to_string())

    return splits, result, selected_sfs


def preparar_datos(forzar: bool = False) -> dict:
    """Devuelve X/y de train y validation ya tratados y listos para modelar.

    `forzar` recalcula el tratamiento; la seleccion cacheada se reutiliza (para
    rehacerla, `--forzar` desde `run_pipeline.py --etapa seleccion`).
    """
    def _construir():
        splits, result, selected_sfs = preparar_seleccion()

        trat = artifacts.cachear(
            "tratamiento",
            lambda: data.TratamientoHorario(selected_sfs).fit(splits["X_train"], result["target_corr"]),
            forzar,
        )

        datos = {
            "X_train": trat.transform(splits["X_train"]),
            "X_val": trat.transform(splits["X_val"]),
            "y_train": data.preparar_target(splits["y_train"]),
            "y_val": data.preparar_target(splits["y_val"]),
        }
        # El indice tiene que casar exactamente: SARIMAX no perdona un desalineo
        for split in ("train", "val"):
            X, y = datos[f"X_{split}"], datos[f"y_{split}"]
            comunes = X.index.intersection(y.index)
            datos[f"X_{split}"] = X.loc[comunes]
            datos[f"y_{split}"] = y.loc[comunes]
            log.info("%s tratado: X %s, y %d horas", split, datos[f"X_{split}"].shape, len(comunes))
        return datos

    return artifacts.cachear("datos_tratados", _construir, forzar)


def preparar_escalados(forzar: bool = False):
    """Lo mismo que `preparar_datos`, mas las X escaladas que necesitan Ridge y
    ElasticNet. El scaler se ajusta solo con train.

    Devuelve (datos, X_train_escalado, X_val_escalado).
    """
    datos = preparar_datos(forzar)
    _, X_train_scaled, X_val_scaled = data.escalar(datos["X_train"], datos["X_val"])
    return datos, X_train_scaled, X_val_scaled
