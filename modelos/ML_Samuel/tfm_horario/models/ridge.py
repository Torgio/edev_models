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
# como modulo (`python -m tfm_horario.models.ridge`). Lanzado como script, Python no
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
    runpy.run_module("tfm_horario.models.ridge", run_name="__main__", alter_sys=True)
    sys.exit(0)

import argparse
import logging

import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error

from .. import ajustes as config, artifacts, data, entrega, preparacion

log = logging.getLogger(__name__)

MODELO_ID = "ridge_horario"      # asi aparecera en el leaderboard
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


def entrenar_y_predecir(X_train, y_train, X_val, y_val, alphas: list | None = None,
                        X_train_crudo=None, X_val_crudo=None):
    """Tunea alpha sobre validation, reentrena con el mejor y predice.

    Devuelve (modelo_final, predicciones, tabla_de_tuning). El alpha se elige
    mirando el MAE de validation, asi que esa metrica queda algo optimista: es el
    mismo split que lo escogio. Con el test sellado se corrige solo.

    El TUNEO usa las X ya escaladas (se escalan una vez y se reaprovechan en los
    diez ajustes de la rejilla). El MODELO FINAL se reajusta sobre las X CRUDAS
    dentro de un pipeline que lleva el StandardScaler dentro, para que el
    `modelo.joblib` acepte la matriz tal y como llega en produccion. Es el mismo
    modelo -- el scaler se ajusta sobre el mismo train --, solo que servible.
    """
    tabla = tunear(X_train, y_train, X_val, y_val, alphas)
    best_alpha = tabla.iloc[0]["alpha"]

    if X_train_crudo is None:
        # Sin las crudas se devuelve el estimador pelado: vale para experimentar
        # desde un notebook, pero ese objeto NO debe acabar en un entregable.
        log.warning("modelo final SIN escalador dentro: no es servible en produccion")
        final = Ridge(alpha=best_alpha)
        final.fit(X_train, y_train)
        return final, pd.Series(final.predict(X_val), index=y_val.index), tabla

    final = data.pipeline_escalado(Ridge(alpha=best_alpha))
    final.fit(X_train_crudo, y_train)
    pred = pd.Series(final.predict(X_val_crudo), index=y_val.index)
    return final, pred, tabla


def coeficientes(modelo, columnas) -> pd.Series:
    """Coeficientes ordenados por magnitud. Sobre features escaladas son
    comparables entre si, asi que sirven para la memoria como lectura de que pesa
    en el precio."""
    return pd.Series(modelo.coef_, index=columnas).sort_values(key=abs, ascending=False)


# ---------------------------------------------------------------------------
def ejecutar(forzar: bool = False, modo: str | None = None) -> pd.Series:
    """Prepara los datos, entrena y deja el entregable en entregables/ridge_horario/."""
    datos, X_train_scaled, X_val_scaled, _ = preparacion.preparar_escalados(modo, forzar)

    modelo, pred, tabla = entrenar_y_predecir(
        X_train_scaled, datos["y_train"], X_val_scaled, datos["y_val"],
        X_train_crudo=datos["X_train"], X_val_crudo=datos["X_val"],
    )

    # El tuning se guarda en salidas/ (uso interno), NO en entregables/
    config.preparar_entorno()
    tabla.to_csv(config.OUTPUT_DIR / f"tuning_ridge_{datos['modo']}.csv", index=False)
    # `modelo` es ahora un Pipeline: el estimador esta en el ultimo paso.
    coeficientes(modelo[-1], datos["X_train"].columns).to_csv(
        config.OUTPUT_DIR / f"coefs_ridge_{datos['modo']}.csv")

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
        notas=(f"alpha={modelo[-1].alpha} elegido por MAE de validation sobre la rejilla "
               f"{config.RIDGE_ALPHAS}; la metrica de validation queda algo optimista "
               "porque es el mismo split que lo eligio. StandardScaler dentro del "
               "artefacto: acepta features crudas."),
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
    log_ = config.configurar_logging("ridge")
    try:
        ejecutar(args.forzar, args.seleccion)
    except Exception:
        log_.exception("Ridge ha fallado")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
