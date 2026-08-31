"""Preparacion de datos comun a los cuatro modelos, con cuatro modos de seleccion.

Encadena lo previo a cualquier entrenamiento:

    matriz_nucleo.csv -> split (UTC) -> seleccion de features -> (escalado)

La seleccion tiene cuatro modos, elegibles con `--seleccion` en cualquier modelo:

    ambos     Spearman y despues SFS sobre los supervivientes  (por defecto)
    spearman  solo el filtro de correlacion
    sfs       solo seleccion secuencial, sobre las 128 features de la matriz
    ninguna   sin seleccion, las 128 features

Cada modo cachea aparte (`datos_<modo>.pkl`, `sfs_solo.pkl` vs
`sfs_tras_spearman.pkl`), asi que se pueden lanzar los cuatro seguidos sin que se
pisen y sin recalcular lo que compartan. Ya no hay etapa de tratamiento de nulos:
la matriz llega depurada.
"""

from __future__ import annotations

import logging

from . import ajustes as config, artifacts, data, selection

log = logging.getLogger(__name__)


def _validar(modo: str) -> str:
    if modo not in config.MODOS_SELECCION:
        raise ValueError(f"modo de seleccion {modo!r}; opciones: {config.MODOS_SELECCION}")
    return modo


def preparar_splits(forzar: bool = False) -> dict:
    """Lee la matriz y la parte en train / validation. Comun a los cuatro modos."""
    splits = artifacts.cachear("splits", lambda: data.cargar_splits(), forzar)
    log.info("train %s | val %s", splits["X_train"].shape, splits["X_val"].shape)
    return splits


def _spearman(X_train, y_train, forzar: bool):
    """Filtro de correlacion. Barato (segundos) y comun a los modos que lo usan."""
    return artifacts.cachear(
        "spearman",
        lambda: selection.select_features_spearman(X_train, y_train),
        forzar,
    )


def _sfs(X_train, y_train, candidatas: list[str], clave: str, forzar: bool):
    """Seleccion secuencial sobre `candidatas`. Es la etapa cara del pipeline."""
    log.info("SFS (%s) sobre %d candidatas", clave, len(candidatas))
    resultado = artifacts.cachear(
        clave,
        lambda: selection.select_features_sfs(X_train[candidatas], y_train),
        forzar,
    )
    return resultado["selected"]


def seleccionar(modo: str | None = None, forzar: bool = False) -> list[str]:
    """Devuelve la lista de features del modo pedido.

    Es la unica funcion que hay que mirar para entender que hace cada modo.
    """
    modo = _validar(modo or config.MODO_SELECCION)
    splits = preparar_splits()
    X_train, y_train = splits["X_train"], splits["y_train"]
    todas = X_train.columns.tolist()

    if modo == "ninguna":
        log.info("SELECCION 'ninguna': las %d features de la matriz, sin filtrar", len(todas))
        return todas

    if modo == "spearman":
        features = _spearman(X_train, y_train, forzar)["selected"]
        log.info("SELECCION 'spearman': %d de %d features", len(features), len(todas))

    elif modo == "sfs":
        # Sin pre-filtro: el SFS arranca con las 128 columnas. Es el modo mas caro.
        log.warning("SELECCION 'sfs': sin pre-filtro de Spearman, el SFS parte de %d "
                    "candidatas. Es el modo mas lento con diferencia.", len(todas))
        features = _sfs(X_train, y_train, todas, "sfs_solo", forzar)
        log.info("SELECCION 'sfs': %d de %d features", len(features), len(todas))

    else:   # ambos
        supervivientes = _spearman(X_train, y_train, forzar)["selected"]
        features = _sfs(X_train, y_train, supervivientes, "sfs_tras_spearman", forzar)
        log.info("SELECCION 'ambos': %d -> %d (Spearman) -> %d (SFS)",
                 len(todas), len(supervivientes), len(features))

    _diagnostico_colinealidad(X_train, features, modo)
    return features


def _diagnostico_colinealidad(X_train, features: list[str], modo: str) -> None:
    """Redundancia que queda entre las features elegidas (antes eran heatmaps)."""
    corr = selection.matriz_correlacion(X_train, features)
    log.info("colinealidad tras '%s': %s", modo, selection.resumen_colinealidad(corr))
    log.info("pares mas correlados:\n%s", selection.pares_mas_correlados(corr, top_n=10).to_string())


def preparar_datos(modo: str | None = None, forzar: bool = False) -> dict:
    """X/y de train y validation, listos para modelar, con el modo pedido."""
    modo = _validar(modo or config.MODO_SELECCION)

    def _construir():
        splits = preparar_splits()
        features = seleccionar(modo, forzar)

        datos = {
            "modo": modo,
            "features": features,
            "X_train": data.aplicar_features(splits["X_train"], features, "train"),
            "X_val": data.aplicar_features(splits["X_val"], features, "val"),
            "y_train": data.preparar_target(splits["y_train"]),
            "y_val": data.preparar_target(splits["y_val"]),
        }
        for split in ("train", "val"):
            log.info("%s: X %s, y %d horas", split, datos[f"X_{split}"].shape, len(datos[f"y_{split}"]))
        return datos

    return artifacts.cachear(f"datos_{modo}", _construir, forzar)


def preparar_escalados(modo: str | None = None, forzar: bool = False):
    """Lo mismo, mas las X escaladas que necesitan Ridge y ElasticNet.

    El scaler se ajusta solo con train. Devuelve (datos, X_train_esc, X_val_esc).
    """
    datos = preparar_datos(modo, forzar)
    _, X_train_scaled, X_val_scaled = data.escalar(datos["X_train"], datos["X_val"])
    return datos, X_train_scaled, X_val_scaled
