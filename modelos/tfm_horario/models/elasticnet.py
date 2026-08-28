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

# Permite ejecutar el fichero como script suelto (`python .../elasticnet.py`) ademas
# de como modulo (`python -m ...`): sin esto, los imports relativos de abajo fallan.
if __package__ in (None, ""):
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    __package__ = "tfm_horario.models"

import argparse
import itertools
import logging

import pandas as pd
from sklearn.linear_model import ElasticNet
from sklearn.metrics import mean_absolute_error

from .. import ajustes as config, artifacts, evaluation, preparacion

log = logging.getLogger(__name__)

NOMBRE = "ElasticNet"


def tunear(X_train, y_train, X_val, y_val, alphas: list | None = None,
           l1_ratios: list | None = None) -> pd.DataFrame:
    """Barre la rejilla alpha x l1_ratio y devuelve la tabla ordenada por MAE."""
    alphas = alphas or config.EN_ALPHAS
    l1_ratios = l1_ratios or config.EN_L1_RATIOS

    filas = []
    for alpha, l1_ratio in itertools.product(alphas, l1_ratios):
        modelo = ElasticNet(alpha=alpha, l1_ratio=l1_ratio, max_iter=config.EN_MAX_ITER)
        modelo.fit(X_train, y_train)
        filas.append({
            "alpha": alpha,
            "l1_ratio": l1_ratio,
            "MAE_val": mean_absolute_error(y_val, modelo.predict(X_val)),
        })

    tabla = pd.DataFrame(filas).sort_values("MAE_val").reset_index(drop=True)
    log.info("Mejor combinacion (ElasticNet): alpha=%s l1_ratio=%s MAE=%.3f",
             tabla.iloc[0]["alpha"], tabla.iloc[0]["l1_ratio"], tabla.iloc[0]["MAE_val"])
    return tabla


def entrenar_y_predecir(X_train, y_train, X_val, y_val, alphas: list | None = None,
                        l1_ratios: list | None = None):
    """Tunea (alpha, l1_ratio) sobre validation, reentrena con la mejor y predice.

    Devuelve (modelo_final, predicciones, tabla_de_tuning). Los hiperparametros se
    eligen mirando el MAE de validation, asi que esa metrica queda algo optimista:
    es el mismo split que los escogio. Con el test sellado se corrige solo.
    """
    tabla = tunear(X_train, y_train, X_val, y_val, alphas, l1_ratios)
    best_alpha = tabla.iloc[0]["alpha"]
    best_l1 = tabla.iloc[0]["l1_ratio"]

    final = ElasticNet(alpha=best_alpha, l1_ratio=best_l1, max_iter=config.EN_MAX_ITER)
    final.fit(X_train, y_train)
    pred = pd.Series(final.predict(X_val), index=y_val.index)
    return final, pred, tabla


def features_anuladas(modelo, columnas) -> list[str]:
    """Features con coeficiente exactamente cero: la parte L1 hace seleccion, y
    saber cuales tira es un resultado que interesa contar en la memoria."""
    coefs = pd.Series(modelo.coef_, index=columnas)
    return coefs[coefs == 0].index.tolist()


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False) -> pd.Series:
    """Prepara los datos, entrena, guarda predicciones y compara con el naive."""
    datos, X_train_scaled, X_val_scaled = preparacion.preparar_escalados(forzar)

    modelo, pred, tabla = entrenar_y_predecir(
        X_train_scaled, datos["y_train"], X_val_scaled, datos["y_val"]
    )

    config.preparar_entorno()
    tabla.to_csv(config.METRICS_DIR / "tuning_elasticnet.csv", index=False)
    anuladas = features_anuladas(modelo, X_train_scaled.columns)
    log.info("ElasticNet anula %d features: %s", len(anuladas), anuladas)

    artifacts.guardar(pred, "pred_elasticnet")
    artifacts.guardar(modelo, "modelo_elasticnet")
    evaluation.informe_modelo(NOMBRE, pred, datos["y_train"], datos["y_val"])
    return pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forzar", action="store_true",
                        help="rehace el tratamiento en vez de leerlo de artifacts/")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("elasticnet")
    try:
        ejecutar(args.forzar)
    except Exception:
        log_.exception("ElasticNet ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
