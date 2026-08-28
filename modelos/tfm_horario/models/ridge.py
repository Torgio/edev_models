"""Ridge: regresion lineal con penalizacion L2 (celdas 42-45 del notebook).

Se puede ejecutar solo. Llama por su cuenta a la preparacion de datos (carga,
split, Spearman + SFS, tratamiento y escalado):

    python -m tfm_horario.models.ridge
    python -m tfm_horario.models.ridge --forzar        # rehace el tratamiento
    python tfm_horario/models/ridge.py                 # tambien vale suelto

El escalado NO esta aqui: `StandardScaler` es tratamiento de datos, lo comparten
Ridge y ElasticNet, y ajustarlo dos veces (una por modelo) seria repetir trabajo.
Vive en `data.escalar` y lo aplica `preparacion.preparar_escalados`.
"""

from __future__ import annotations

# Permite ejecutar el fichero como script suelto (`python .../ridge.py`) ademas de
# como modulo (`python -m ...`): sin esto, los imports relativos de abajo fallan.
if __package__ in (None, ""):
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    __package__ = "tfm_horario.models"

import argparse
import logging

import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error

from .. import ajustes as config, artifacts, evaluation, preparacion

log = logging.getLogger(__name__)

NOMBRE = "Ridge"


def tunear(X_train, y_train, X_val, y_val, alphas: list | None = None) -> pd.DataFrame:
    """Barre la rejilla de alphas y devuelve la tabla ordenada por MAE de validation."""
    alphas = alphas or config.RIDGE_ALPHAS

    filas = []
    for alpha in alphas:
        modelo = Ridge(alpha=alpha)
        modelo.fit(X_train, y_train)
        filas.append({"alpha": alpha, "MAE_val": mean_absolute_error(y_val, modelo.predict(X_val))})

    tabla = pd.DataFrame(filas).sort_values("MAE_val").reset_index(drop=True)
    log.info("Mejor alpha (Ridge): %s  MAE=%.3f", tabla.iloc[0]["alpha"], tabla.iloc[0]["MAE_val"])
    return tabla


def entrenar_y_predecir(X_train, y_train, X_val, y_val, alphas: list | None = None):
    """Tunea alpha sobre validation, reentrena con el mejor y predice.

    Devuelve (modelo_final, predicciones, tabla_de_tuning). El alpha se elige
    mirando el MAE de validation, asi que esa metrica queda algo optimista: es el
    mismo split que lo escogio. Con el test sellado se corrige solo.
    """
    tabla = tunear(X_train, y_train, X_val, y_val, alphas)
    best_alpha = tabla.iloc[0]["alpha"]

    final = Ridge(alpha=best_alpha)
    final.fit(X_train, y_train)
    pred = pd.Series(final.predict(X_val), index=y_val.index)
    return final, pred, tabla


def coeficientes(modelo, columnas) -> pd.Series:
    """Coeficientes ordenados por magnitud. Sobre features escaladas son
    comparables entre si, asi que sirven para la memoria como lectura de que pesa
    en el precio."""
    return pd.Series(modelo.coef_, index=columnas).sort_values(key=abs, ascending=False)


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False) -> pd.Series:
    """Prepara los datos, entrena, guarda predicciones y compara con el naive."""
    datos, X_train_scaled, X_val_scaled = preparacion.preparar_escalados(forzar)

    modelo, pred, tabla = entrenar_y_predecir(
        X_train_scaled, datos["y_train"], X_val_scaled, datos["y_val"]
    )

    config.preparar_entorno()
    tabla.to_csv(config.METRICS_DIR / "tuning_ridge.csv", index=False)
    coeficientes(modelo, X_train_scaled.columns).to_csv(config.METRICS_DIR / "coefs_ridge.csv")

    artifacts.guardar(pred, "pred_ridge")
    artifacts.guardar(modelo, "modelo_ridge")
    evaluation.informe_modelo(NOMBRE, pred, datos["y_train"], datos["y_val"])
    return pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forzar", action="store_true",
                        help="rehace el tratamiento en vez de leerlo de artifacts/")
    args = parser.parse_args()

    config.preparar_entorno()
    log_ = config.configurar_logging("ridge")
    try:
        ejecutar(args.forzar)
    except Exception:
        log_.exception("Ridge ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
