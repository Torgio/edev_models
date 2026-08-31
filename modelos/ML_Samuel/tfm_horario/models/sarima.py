"""SARIMA sin exogenas (celdas 26-31 del notebook).

Este modulo contiene ademas el motor que comparte con SARIMAX: la busqueda de
orden, el ajuste y las tres estrategias de prediccion admiten `exog` opcional.
`sarimax.py` los reutiliza en vez de duplicarlos -- son el mismo SARIMAX de
statsmodels, y mantener dos copias del bucle walk-forward garantizaba que tarde o
temprano divergieran.

Del notebook: habia tres celdas alternativas que calculaban `predictions` de formas
distintas (28, 29 y 30) y se pisaban entre si segun el orden de ejecucion. Aqui son
estrategias explicitas elegidas por parametro.

Se puede ejecutar solo. Llama por su cuenta a la preparacion de datos:

    python -m tfm_horario.models.sarima
    python -m tfm_horario.models.sarima --estrategia directo
    python tfm_horario/models/sarima.py                 # tambien vale suelto
"""

from __future__ import annotations

# Permite ejecutar el fichero como script suelto (`python .../sarima.py`) ademas de
# como modulo (`python -m tfm_horario.models.sarima`). Lanzado como script, Python no
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
    runpy.run_module("tfm_horario.models.sarima", run_name="__main__", alter_sys=True)
    sys.exit(0)

import argparse
import logging

import numpy as np
import pandas as pd
import pmdarima as pm
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .. import ajustes as config, artifacts, entrega, preparacion

log = logging.getLogger(__name__)

ESTRATEGIAS = config.ESTRATEGIAS
MODELO_ID = "sarima_horario"     # asi aparecera en el leaderboard
NOMBRE = "SARIMA (sin exog)"


def buscar_orden(
    y_train: pd.Series,
    X_train: pd.DataFrame | None = None,
    ventana: int = config.VENTANA_ORDEN,
    **kwargs,
):
    """Busca (p,d,q)(P,D,Q,m) con auto_arima.

    CAMBIO CLAVE respecto al dataset diario: m=24, no m=7. En horario el ciclo
    dominante es el del dia; el semanal (m=168) es inviable en SARIMA -- si se
    quiere capturar, se deja en manos de `dow` / `is_weekend` y los lags 168h.

    auto_arima con m=24 sobre decenas de miles de horas es MUY lento (cada fit
    estima una matriz de estado de tamaño ~m), asi que el orden se busca sobre la
    ventana MAS RECIENTE de train y despues se ajusta con todo train.

    `X=` en vez de `exogenous=`: el segundo esta deprecado en pmdarima >= 1.8.
    """
    params = {**config.AUTO_ARIMA_PARAMS, **kwargs}
    y_ventana = y_train.iloc[-ventana:] if ventana else y_train
    X_ventana = None
    if X_train is not None:
        X_ventana = X_train.iloc[-ventana:] if ventana else X_train

    log.info("auto_arima sobre %d horas (%s exogenas)", len(y_ventana), "con" if X_ventana is not None else "sin")
    auto = pm.auto_arima(y_ventana, X=X_ventana, **params)
    log.info("orden encontrado: %s %s", auto.order, auto.seasonal_order)
    return auto.order, auto.seasonal_order


def recortar_historia(y_train: pd.Series, exog: pd.DataFrame | None = None):
    """Deja solo las ultimas `SARIMA_MAX_HORAS_TRAIN` horas, si esta configurado."""
    tope = config.SARIMA_MAX_HORAS_TRAIN
    if not tope or len(y_train) <= tope:
        return y_train, exog
    log.info("recortando train de %d a %d horas (SARIMA_MAX_HORAS_TRAIN)", len(y_train), tope)
    return y_train.iloc[-tope:], (exog.iloc[-tope:] if exog is not None else None)


def ajustar(
    y_train: pd.Series,
    order,
    seasonal_order,
    exog: pd.DataFrame | None = None,
):
    """Ajusta el modelo sobre train con el orden ya elegido.

    `low_memory=True` no es un detalle: sin el, statsmodels guarda las matrices del
    filtro de Kalman para cada una de las ~44.000 horas y el proceso muere por OOM
    (ver SARIMA_LOW_MEMORY en ajustes.py).
    """
    y_train, exog = recortar_historia(y_train, exog)

    modelo = SARIMAX(
        y_train,
        exog=exog,
        order=order,
        seasonal_order=seasonal_order,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    log.info("ajustando SARIMAX%s%s sobre %d horas%s (k_states=%d, low_memory=%s)",
             order, seasonal_order, len(y_train),
             f" con {exog.shape[1]} exogenas" if exog is not None else " sin exogenas",
             modelo.k_states, config.SARIMA_LOW_MEMORY)

    fit = modelo.fit(disp=False, low_memory=config.SARIMA_LOW_MEMORY)

    if not config.SARIMA_LOW_MEMORY or not config.SARIMA_VENTANA_ESTADO:
        return fit

    # Con low_memory los parametros son correctos pero no se ha guardado el estado
    # del filtro, y sin estado `extend()` no puede continuar la serie. Se recupera
    # re-filtrando solo la ventana final con esos mismos parametros: barato, y el
    # estado de un modelo con m=24 ya ha convergido de sobra en 30 dias.
    v = min(config.SARIMA_VENTANA_ESTADO, len(y_train))
    log.info("reconstruyendo el estado del filtro sobre las ultimas %d horas", v)
    ventana = SARIMAX(
        y_train.iloc[-v:],
        exog=exog.iloc[-v:] if exog is not None else None,
        order=order,
        seasonal_order=seasonal_order,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    return ventana.filter(fit.params)


def predecir(
    fit,
    y_objetivo: pd.Series,
    exog_objetivo: pd.DataFrame | None = None,
    estrategia: str = "directo",
) -> pd.Series:
    """Prediccion sobre el split de evaluacion con una de las tres estrategias.

    - "directo":   el modelo ajustado con train predice las 8760 horas de un tiron,
                   sin ver ningun valor real de 2025.
    - "bloques24": cada dia se predice entero (horizonte 1-24h) y despues se le da al
                   modelo el dia observado. Es la simulacion honesta del mercado
                   diario: casa las 24 horas de D+1 con informacion hasta D.

    "walkforward" (hora a hora, horizonte 1h) se ha eliminado: media una tarea que
    nadie realiza -- nadie predice las 14:00 conociendo el precio de las 13:00 --,
    daba metricas infladas y tardaba mas de una hora por año.
    """
    if estrategia not in ESTRATEGIAS:
        raise ValueError(f"estrategia debe ser una de {ESTRATEGIAS}, no {estrategia!r}")

    if estrategia == "directo":
        kwargs = {"exog": exog_objetivo} if exog_objetivo is not None else {}
        pred = fit.forecast(steps=len(y_objetivo), **kwargs)
        return pd.Series(np.asarray(pred), index=y_objetivo.index)

    # bloques24: cada dia se predice entero y despues se le da el dia observado.
    #
    # `extend()` y no `append()`: append re-filtra TODA la serie acumulada en cada
    # iteracion, asi que la memoria crece dia a dia (medido: 2 GB en 30 dias, y el
    # OOM killer esperando). extend continua desde el estado y solo procesa el bloque
    # nuevo, con memoria constante. Las predicciones son identicas.
    bloques = []
    current_fit = fit
    for ini in range(0, len(y_objetivo), 24):
        bloque = y_objetivo.iloc[ini:ini + 24]
        exog_bloque = exog_objetivo.iloc[ini:ini + 24] if exog_objetivo is not None else None

        pred = current_fit.forecast(steps=len(bloque), exog=exog_bloque)
        bloques.append(pd.Series(np.asarray(pred), index=bloque.index))

        current_fit = current_fit.extend(bloque, exog=exog_bloque)
        if (ini // 24 + 1) % 50 == 0:
            log.info("  %d/%d dias", ini // 24 + 1, len(y_objetivo) // 24)
    return pd.concat(bloques)

# ---------------------------------------------------------------------------
def entrenar_y_predecir(
    y_train: pd.Series,
    y_objetivo: pd.Series,
    order=None,
    seasonal_order=None,
    estrategia: str | None = None,
) -> pd.Series:
    """Atajo de una llamada: busca orden (si no se le da), ajusta y predice.

    `order`/`seasonal_order` se pasan cuando ya estan cacheados en disco, para no
    repetir el auto_arima, que es la parte cara.
    """
    if order is None or seasonal_order is None:
        order, seasonal_order = buscar_orden(y_train)
    fit = ajustar(y_train, order, seasonal_order)
    return predecir(fit, y_objetivo, estrategia=estrategia or config.ESTRATEGIA_SARIMA)


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False, estrategia: str | None = None,
             modo: str | None = None) -> pd.Series:
    """Prepara los datos, busca orden (cacheado), ajusta, predice y compara con el naive."""
    datos = preparacion.preparar_datos(modo, forzar)
    estrategia = estrategia or config.ESTRATEGIA_SARIMA

    # El orden se cachea aparte: es lo caro, y no cambia al probar otra estrategia
    order, seasonal_order = artifacts.cachear(
        "orden_sarima",
        lambda: buscar_orden(datos["y_train"]),
        forzar,
    )
    fit = ajustar(datos["y_train"], order, seasonal_order)
    pred = predecir(fit, datos["y_val"], estrategia=estrategia)

    modelo_id = entrega.id_con_modo(MODELO_ID, datos["modo"])
    artifacts.guardar(pred, f"pred_{modelo_id}")
    log.info("SARIMA listo (estrategia=%s)", estrategia)
    entrega.guardar_entregable(
        modelo_id,
        modelo=fit,
        pred=pred,
        features=[],                      # SARIMA sin exogenas: solo usa el historico del precio
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
                        help=f"por defecto, la de config ({config.ESTRATEGIA_SARIMA})")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("sarima")
    try:
        ejecutar(args.forzar, args.estrategia, args.seleccion)
    except Exception:
        log_.exception("SARIMA ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
