"""ElasticNet: penalizacion L1 + L2 (celdas 48-49 del notebook).

Se puede ejecutar solo. Llama por su cuenta a la preparacion de datos (carga,
split, Spearman + SFS, tratamiento y escalado), asi que no necesita el
orquestador:

    python -m tfm_horario.models.elasticnet
    python -m tfm_horario.models.elasticnet --forzar        # rehace el tratamiento
    python tfm_horario/models/elasticnet.py                 # tambien vale suelto

`l1_ratio=1.0` es Lasso puro, asi que la rejilla de config cubre tambien ese caso
sin necesidad de un script aparte.
"""

from __future__ import annotations

# Permite ejecutar el fichero como script suelto (`python .../elasticnet.py`) ademas de
# como modulo (`python -m tfm_horario.models.elasticnet`). Lanzado como script, Python no
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
    runpy.run_module("tfm_horario.models.elasticnet", run_name="__main__", alter_sys=True)
    sys.exit(0)

import argparse
import itertools
import logging
import warnings

import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet
from sklearn.metrics import mean_absolute_error

from .. import ajustes as config, artifacts, data, entrega, preparacion

log = logging.getLogger(__name__)

MODELO_ID = "elasticnet_horario"   # asi aparecera en el leaderboard
NOMBRE = "ElasticNet"


def tunear(X_train, y_train, X_val, y_val, alphas: list | None = None,
           l1_ratios: list | None = None) -> pd.DataFrame:
    """Barre la rejilla alpha x l1_ratio y devuelve la tabla ordenada por MAE."""
    alphas = alphas or config.EN_ALPHAS
    l1_ratios = l1_ratios or config.EN_L1_RATIOS

    filas = []
    for alpha, l1_ratio in itertools.product(alphas, l1_ratios):
        modelo = ElasticNet(alpha=alpha, l1_ratio=l1_ratio, max_iter=config.EN_MAX_ITER)

        # Se captura el ConvergenceWarning de cada ajuste en vez de dejar que
        # inunde la consola: interesa SABER que combinaciones no convergieron, no
        # ver el mismo aviso 30 veces sin saber a cual corresponde.
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always", ConvergenceWarning)
            modelo.fit(X_train, y_train)
            convergio = not any(issubclass(a.category, ConvergenceWarning) for a in avisos)

        filas.append({
            "alpha": alpha,
            "l1_ratio": l1_ratio,
            "MAE_val": mean_absolute_error(y_val, modelo.predict(X_val)),
            "n_iter": int(modelo.n_iter_),
            "convergio": convergio,
        })

    tabla = pd.DataFrame(filas).sort_values("MAE_val").reset_index(drop=True)

    sin_converger = tabla[~tabla["convergio"]]
    if len(sin_converger):
        log.warning("%d de %d combinaciones NO convergieron en %d iteraciones. "
                    "Suelen ser los alphas mas pequeños sobre features colineales; "
                    "sube EN_MAX_ITER en ajustes.py o usa un modo con seleccion.",
                    len(sin_converger), len(tabla), config.EN_MAX_ITER)
        log.warning("no convergen:\n%s",
                    sin_converger[["alpha", "l1_ratio", "n_iter"]].to_string(index=False))

    mejor = tabla.iloc[0]
    log.info("Mejor combinacion (ElasticNet): alpha=%s l1_ratio=%s MAE=%.3f (n_iter=%d)",
             mejor["alpha"], mejor["l1_ratio"], mejor["MAE_val"], mejor["n_iter"])
    return tabla


def entrenar_y_predecir(X_train, y_train, X_val, y_val, alphas: list | None = None,
                        l1_ratios: list | None = None,
                        X_train_crudo=None, X_val_crudo=None):
    """Tunea (alpha, l1_ratio) sobre validation, reentrena con la mejor y predice.

    Devuelve (modelo_final, predicciones, tabla_de_tuning). Los hiperparametros se
    eligen mirando el MAE de validation, asi que esa metrica queda algo optimista:
    es el mismo split que los escogio. Con el test sellado se corrige solo.

    El tuneo va sobre X escaladas (una vez, reaprovechadas en las 60 combinaciones
    de la rejilla); el modelo final se reajusta sobre X crudas dentro de un
    pipeline con el StandardScaler dentro, para que el artefacto sea servible. Ver
    `data.pipeline_escalado`.
    """
    tabla = tunear(X_train, y_train, X_val, y_val, alphas, l1_ratios)

    # Solo se elige entre las combinaciones que CONVERGIERON. Un ajuste que se
    # quedo a medias no es el modelo que dice ser: sus coeficientes dependen de
    # donde se corto la optimizacion, asi que no es reproducible y no deberia
    # acabar en el entregable aunque su MAE salga bajo.
    convergidas = tabla[tabla["convergio"]]
    if len(convergidas):
        if len(convergidas) < len(tabla):
            log.info("se elige entre las %d combinaciones convergidas de %d",
                     len(convergidas), len(tabla))
        mejor = convergidas.iloc[0]
    else:
        log.error("NINGUNA combinacion convergio: se usa la de menor MAE, pero el "
                  "modelo NO es reproducible. Sube EN_MAX_ITER antes de entregarlo.")
        mejor = tabla.iloc[0]

    best_alpha = mejor["alpha"]
    best_l1 = mejor["l1_ratio"]

    estimador = ElasticNet(alpha=best_alpha, l1_ratio=best_l1, max_iter=config.EN_MAX_ITER)

    if X_train_crudo is None:
        log.warning("modelo final SIN escalador dentro: no es servible en produccion")
        estimador.fit(X_train, y_train)
        return estimador, pd.Series(estimador.predict(X_val), index=y_val.index), tabla

    final = data.pipeline_escalado(estimador)
    # El ajuste final tambien puede no converger, y aqui ya no hay rejilla que
    # mirar: si pasa, el artefacto que se sube no es el modelo que dice ser.
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always", ConvergenceWarning)
        final.fit(X_train_crudo, y_train)
        if any(issubclass(a.category, ConvergenceWarning) for a in avisos):
            raise RuntimeError(
                f"el ajuste final (alpha={best_alpha}, l1_ratio={best_l1}) no convergio "
                f"en {config.EN_MAX_ITER} iteraciones. Sus coeficientes dependen de donde "
                "se corto la optimizacion, asi que no es reproducible y no debe entregarse. "
                "Sube EN_MAX_ITER en ajustes.py y relanza."
            )
    pred = pd.Series(final.predict(X_val_crudo), index=y_val.index)
    return final, pred, tabla


def features_anuladas(modelo, columnas) -> list[str]:
    """Features con coeficiente exactamente cero: la parte L1 hace seleccion, y
    saber cuales tira es un resultado que interesa contar en la memoria."""
    coefs = pd.Series(modelo.coef_, index=columnas)
    return coefs[coefs == 0].index.tolist()


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False, modo: str | None = None) -> pd.Series:
    """Prepara los datos, entrena y deja el entregable en entregables/elasticnet_horario/."""
    datos, X_train_scaled, X_val_scaled, _ = preparacion.preparar_escalados(modo, forzar)

    modelo, pred, tabla = entrenar_y_predecir(
        X_train_scaled, datos["y_train"], X_val_scaled, datos["y_val"],
        X_train_crudo=datos["X_train"], X_val_crudo=datos["X_val"],
    )

    # El tuning se guarda en salidas/ (uso interno), NO en entregables/
    config.preparar_entorno()
    tabla.to_csv(config.OUTPUT_DIR / f"tuning_elasticnet_{datos['modo']}.csv", index=False)
    # `modelo` es ahora un Pipeline: el estimador esta en el ultimo paso.
    anuladas = features_anuladas(modelo[-1], datos["X_train"].columns)
    log.info("ElasticNet anula %d features: %s", len(anuladas), anuladas)

    modelo_id = entrega.id_con_modo(MODELO_ID, datos["modo"])
    artifacts.guardar(pred, f"pred_{modelo_id}")
    entrega.guardar_entregable(
        modelo_id,
        modelo=modelo,
        pred=pred,
        features=list(datos["X_train"].columns),
        librerias=entrega.librerias_de("scikit-learn"),
        entrenado_desde=entrega.fecha_inicio(datos["y_train"]),
        modo_seleccion=datos["modo"],
        notas=(f"alpha={modelo[-1].alpha}, l1_ratio={modelo[-1].l1_ratio} elegidos por MAE "
               f"de validation entre las combinaciones convergidas; anula {len(anuladas)} "
               "features via L1. StandardScaler dentro del artefacto: acepta features crudas."),
        X_control=datos["X_val"],
    )
    return pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seleccion", choices=config.MODOS_SELECCION, default=None,
                        help=f"modo de seleccion de features (por defecto {config.MODO_SELECCION})")
    parser.add_argument("--forzar", action="store_true",
                        help="rehace el tratamiento en vez de leerlo de artifacts/")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("elasticnet")
    try:
        ejecutar(args.forzar, args.seleccion)
    except Exception:
        log_.exception("ElasticNet ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
