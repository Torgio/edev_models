"""SARIMAX: SARIMA + variables exogenas (celdas 33-39 del notebook).

Las exogenas son las features que sobrevivieron a Spearman + SFS y al tratamiento
de huecos, ya alineadas hora a hora con el target.

El motor (auto_arima, ajuste y las tres estrategias de prediccion) se reutiliza de
`sarima.py`: es literalmente el mismo `SARIMAX` de statsmodels, solo cambia que
aqui `exog` es obligatorio. Lo que aporta este modulo es una API que no deja
llamarlo sin exogenas por descuido -- en el notebook la diferencia entre el
modelo con y sin exogenas era solo acordarse de pasar `X=` o `exog=`, y las dos
versiones escribian sobre variables de nombre parecido.

Se puede ejecutar solo. Llama por su cuenta a la preparacion de datos:

    python -m tfm_horario.models.sarimax
    python -m tfm_horario.models.sarimax --estrategia bloques24
    python tfm_horario/models/sarimax.py                # tambien vale suelto
"""

from __future__ import annotations

# Permite ejecutar el fichero como script suelto (`python .../sarimax.py`) ademas de
# como modulo (`python -m tfm_horario.models.sarimax`). Lanzado como script, Python no
# lo considera parte del paquete y los imports relativos de abajo fallan, asi que
# lo relanzamos a traves del sistema de importacion normal.
#
# La condicion mira `__spec__` y no `__package__`: asignar `__package__` a mano
# funcionaba hasta Python 3.13, pero 3.14 lo ignora en favor de `__spec__.parent`,
# y el sintoma era "attempted relative import beyond top-level package".
if __name__ == "__main__" and __spec__ is None:
    import pathlib
    import runpy
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    runpy.run_module("tfm_horario.models.sarimax", run_name="__main__", alter_sys=True)
    sys.exit(0)

import argparse
import logging

import pandas as pd

from .. import ajustes as config, artifacts, entrega, preparacion
from .sarima import ESTRATEGIAS, ajustar as _ajustar, buscar_orden as _buscar_orden, predecir as _predecir

log = logging.getLogger(__name__)

MODELO_ID = "sarimax_horario"    # asi aparecera en el leaderboard
NOMBRE = "SARIMAX (+exog)"

__all__ = ["ESTRATEGIAS", "NOMBRE", "buscar_orden", "ajustar", "predecir", "entrenar_y_predecir", "ejecutar"]


def _validar_exog(y: pd.Series, X: pd.DataFrame, etiqueta: str) -> None:
    if X is None:
        raise ValueError(f"SARIMAX necesita exogenas en {etiqueta}; para el modelo sin exogenas usa models.sarima")
    if len(X) != len(y):
        raise ValueError(f"{etiqueta}: y tiene {len(y)} horas y X tiene {len(X)}")
    if not X.index.equals(y.index):
        raise ValueError(f"{etiqueta}: el indice de X no coincide con el de y (SARIMAX no perdona un desalineo)")


def buscar_orden(y_train: pd.Series, X_train: pd.DataFrame, ventana: int = config.VENTANA_ORDEN, **kwargs):
    """Busca (p,d,q)(P,D,Q,m) CON las exogenas incluidas, sobre la ventana reciente
    de train. El orden optimo no tiene por que coincidir con el del SARIMA sin
    exogenas: parte de la estructura temporal la explican ya las features."""
    _validar_exog(y_train, X_train, "train")
    return _buscar_orden(y_train, X_train, ventana, **kwargs)


def ajustar(y_train: pd.Series, order, seasonal_order, exog: pd.DataFrame):
    """Ajusta sobre TODO train con el orden ya encontrado."""
    _validar_exog(y_train, exog, "train")
    return _ajustar(y_train, order, seasonal_order, exog=exog)


def predecir(fit, y_objetivo: pd.Series, exog_objetivo: pd.DataFrame, estrategia: str | None = None) -> pd.Series:
    """Predice sobre el split de evaluacion. Las exogenas de ese split son
    obligatorias: sin ellas el modelo no puede proyectar ni un paso.

    Estrategias disponibles: ver el docstring de `sarima.predecir`.
    """
    _validar_exog(y_objetivo, exog_objetivo, "objetivo")
    return _predecir(fit, y_objetivo, exog_objetivo, estrategia or config.ESTRATEGIA_SARIMAX)


# ---------------------------------------------------------------------------
def entrenar_y_predecir(
    y_train: pd.Series,
    X_train: pd.DataFrame,
    y_objetivo: pd.Series,
    X_objetivo: pd.DataFrame,
    order=None,
    seasonal_order=None,
    estrategia: str | None = None,
) -> pd.Series:
    """Atajo de una llamada: busca orden (si no se le da), ajusta y predice."""
    if order is None or seasonal_order is None:
        order, seasonal_order = buscar_orden(y_train, X_train)
    fit = ajustar(y_train, order, seasonal_order, exog=X_train)
    return predecir(fit, y_objetivo, X_objetivo, estrategia)


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False, estrategia: str | None = None,
             modo: str | None = None) -> pd.Series:
    """Prepara los datos, busca orden (cacheado), ajusta, predice y compara con el naive."""
    datos = preparacion.preparar_datos(modo, forzar)
    estrategia = estrategia or config.ESTRATEGIA_SARIMAX

    # Orden propio: con exogenas no tiene por que coincidir con el del SARIMA
    order, seasonal_order = artifacts.cachear(
        f"orden_sarimax_{datos['modo']}",
        lambda: buscar_orden(datos["y_train"], datos["X_train"]),
        forzar,
    )
    fit = ajustar(datos["y_train"], order, seasonal_order, exog=datos["X_train"])
    pred = predecir(fit, datos["y_val"], datos["X_val"], estrategia=estrategia)

    modelo_id = entrega.id_con_modo(MODELO_ID, datos["modo"])
    artifacts.guardar(pred, f"pred_{modelo_id}")
    log.info("SARIMAX listo (estrategia=%s)", estrategia)
    entrega.guardar_entregable(
        modelo_id,
        modelo=fit,
        pred=pred,
        features=list(datos["X_train"].columns),
        librerias=entrega.librerias_de("statsmodels", "pmdarima"),
        entrenado_desde=entrega.fecha_inicio(datos["y_train"]),
    )
    return pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seleccion", choices=config.MODOS_SELECCION, default=None,
                        help=f"modo de seleccion de features (por defecto {config.MODO_SELECCION})")
    parser.add_argument("--forzar", action="store_true",
                        help="rehace tratamiento y busqueda de orden en vez de leerlos de artifacts/")
    parser.add_argument("--estrategia", choices=config.ESTRATEGIAS, default=None,
                        help=f"por defecto, la de config ({config.ESTRATEGIA_SARIMAX})")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("sarimax")
    try:
        ejecutar(args.forzar, args.estrategia, args.seleccion)
    except Exception:
        log_.exception("SARIMAX ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
