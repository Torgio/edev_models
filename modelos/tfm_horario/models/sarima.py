"""SARIMA sin exogenas (celdas 26-31 del notebook).

Este modulo contiene ademas el motor que comparte con SARIMAX: la busqueda de
orden, el ajuste y las tres estrategias de prediccion admiten `exog` opcional.
`sarimax.py` los reutiliza en vez de duplicarlos -- son el mismo SARIMAX de
statsmodels, y mantener dos copias del bucle walk-forward garantizaba que tarde o
temprano divergieran.

Del notebook: habia TRES celdas alternativas que calculaban `predictions` de tres
formas distintas (28, 29 y 30) y se pisaban entre si segun el orden de ejecucion.
Aqui son tres estrategias explicitas elegidas por parametro.

Se puede ejecutar solo. Llama por su cuenta a la preparacion de datos:

    python -m tfm_horario.models.sarima
    python -m tfm_horario.models.sarima --estrategia walkforward
    python tfm_horario/models/sarima.py                 # tambien vale suelto
"""

from __future__ import annotations

# Permite ejecutar el fichero como script suelto ademas de como modulo: sin esto,
# los imports relativos de abajo fallan.
if __package__ in (None, ""):
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    __package__ = "tfm_horario.models"

import argparse
import logging

import numpy as np
import pandas as pd
import pmdarima as pm
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .. import ajustes as config, artifacts, evaluation, preparacion

log = logging.getLogger(__name__)

ESTRATEGIAS = config.ESTRATEGIAS
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


def ajustar(
    y_train: pd.Series,
    order,
    seasonal_order,
    exog: pd.DataFrame | None = None,
):
    """Ajusta el modelo sobre TODO train (hasta 31-dic-2024) con el orden ya elegido."""
    modelo = SARIMAX(
        y_train,
        exog=exog,
        order=order,
        seasonal_order=seasonal_order,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    log.info("ajustando SARIMAX%s%s sobre %d horas%s", order, seasonal_order, len(y_train),
             f" con {exog.shape[1]} exogenas" if exog is not None else " sin exogenas")
    return modelo.fit(disp=False)


def predecir(
    fit,
    y_objetivo: pd.Series,
    exog_objetivo: pd.DataFrame | None = None,
    estrategia: str = "directo",
) -> pd.Series:
    """Prediccion sobre el split de evaluacion con una de las tres estrategias.

    - "directo":     el modelo ajustado con train predice las ~8760 horas de un
                     tiron, sin ver ningun valor real del periodo evaluado.
    - "bloques24":   cada dia se predice entero y despues se le da al modelo el dia
                     observado. 365 iteraciones en vez de 8760.
    - "walkforward": hora a hora, con `refit=False` (parametros fijos, solo se
                     actualiza el estado -- es lo unico que lo hace viable).

    Ojo: "bloques24" y "walkforward" van consumiendo los valores reales del split
    evaluado. Es legitimo como simulacion operativa (cada dia conoces el precio de
    ayer), pero NO es comparable con "directo" en igualdad de condiciones: dilo en
    la memoria al reportar las metricas.
    """
    if estrategia not in ESTRATEGIAS:
        raise ValueError(f"estrategia debe ser una de {ESTRATEGIAS}, no {estrategia!r}")

    if estrategia == "directo":
        kwargs = {"exog": exog_objetivo} if exog_objetivo is not None else {}
        pred = fit.forecast(steps=len(y_objetivo), **kwargs)
        return pd.Series(np.asarray(pred), index=y_objetivo.index)

    if estrategia == "bloques24":
        bloques = []
        current_fit = fit
        for ini in range(0, len(y_objetivo), 24):
            bloque = y_objetivo.iloc[ini:ini + 24]
            if exog_objetivo is not None:
                exog_bloque = exog_objetivo.iloc[ini:ini + 24]
                pred = current_fit.forecast(steps=len(bloque), exog=exog_bloque)
                current_fit = current_fit.append(bloque, exog=exog_bloque, refit=False)
            else:
                pred = current_fit.forecast(steps=len(bloque))
                current_fit = current_fit.append(bloque, refit=False)
            bloques.append(pd.Series(np.asarray(pred), index=bloque.index))
            if (ini // 24 + 1) % 50 == 0:
                log.info("  %d/%d dias", ini // 24 + 1, len(y_objetivo) // 24)
        return pd.concat(bloques)

    # walkforward
    predicciones = []
    current_fit = fit
    for t in range(len(y_objetivo)):
        if exog_objetivo is not None:
            exog_t = exog_objetivo.iloc[[t]]
            pred = current_fit.forecast(steps=1, exog=exog_t)
            current_fit = current_fit.append([y_objetivo.iloc[t]], exog=exog_t, refit=False)
        else:
            pred = current_fit.forecast(steps=1)
            current_fit = current_fit.append([y_objetivo.iloc[t]], refit=False)
        predicciones.append(np.asarray(pred)[0])
        if (t + 1) % 500 == 0:
            log.info("  %d/%d horas", t + 1, len(y_objetivo))
    return pd.Series(predicciones, index=y_objetivo.index)


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
def ejecutar(forzar: bool = False, estrategia: str | None = None) -> pd.Series:
    """Prepara los datos, busca orden (cacheado), ajusta, predice y compara con el naive."""
    datos = preparacion.preparar_datos(forzar)
    estrategia = estrategia or config.ESTRATEGIA_SARIMA

    # El orden se cachea aparte: es lo caro, y no cambia al probar otra estrategia
    order, seasonal_order = artifacts.cachear(
        "orden_sarima",
        lambda: buscar_orden(datos["y_train"]),
        forzar,
    )
    fit = ajustar(datos["y_train"], order, seasonal_order)
    pred = predecir(fit, datos["y_val"], estrategia=estrategia)

    artifacts.guardar(pred, "pred_sarima")
    log.info("SARIMA listo (estrategia=%s)", estrategia)
    evaluation.informe_modelo(NOMBRE, pred, datos["y_train"], datos["y_val"])
    return pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forzar", action="store_true",
                        help="rehace tratamiento y busqueda de orden en vez de leerlos de artifacts/")
    parser.add_argument("--estrategia", choices=config.ESTRATEGIAS, default=None,
                        help=f"por defecto, la de config ({config.ESTRATEGIA_SARIMA})")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("sarima")
    try:
        ejecutar(args.forzar, args.estrategia)
    except Exception:
        log_.exception("SARIMA ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
