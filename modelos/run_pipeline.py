#!/usr/bin/env python3
"""Orquestador: lanza los cuatro modelos y la comparativa final.

Es OPCIONAL. Cada modelo se ejecuta solo y prepara sus propios datos:

    python -m tfm_horario.models.elasticnet
    python -m tfm_horario.models.sarimax --estrategia bloques24

Este script solo sirve para encadenarlos de una vez y sacar la tabla comparativa
con los cuatro juntos:

    python run_pipeline.py                          # los cuatro + comparativa
    python run_pipeline.py --modelos ridge elasticnet
    python run_pipeline.py --solo-evaluacion        # recalcula la tabla con lo que haya
    python run_pipeline.py --forzar                 # rehace tambien seleccion y tratamiento

La preparacion (carga, split, Spearman + SFS, tratamiento) la comparten todos y se
cachea en `salidas/artifacts/`: la paga el primer modelo que corra y los demas la
reutilizan.
"""

from __future__ import annotations

import argparse
import sys

from tfm_horario import ajustes as config, artifacts, evaluation, preparacion

MODELOS = ["sarima", "sarimax", "ridge", "elasticnet"]

# Cada modelo: (modulo, etiqueta en la tabla, artifact con sus predicciones).
# Los import van dentro de la funcion porque sarima/sarimax arrastran pmdarima y
# statsmodels: asi una pasada de solo Ridge/ElasticNet no los necesita instalados.
ARTIFACT_DE = {
    "sarima": "pred_sarima",
    "sarimax": "pred_sarimax",
    "ridge": "pred_ridge",
    "elasticnet": "pred_elasticnet",
}


def _ejecutar_modelo(nombre: str, forzar: bool, estrategias: dict) -> None:
    if nombre == "sarima":
        from tfm_horario.models import sarima
        sarima.ejecutar(forzar, estrategias.get("sarima"))
    elif nombre == "sarimax":
        from tfm_horario.models import sarimax
        sarimax.ejecutar(forzar, estrategias.get("sarimax"))
    elif nombre == "ridge":
        from tfm_horario.models import ridge
        ridge.ejecutar(forzar)
    elif nombre == "elasticnet":
        from tfm_horario.models import elasticnet
        elasticnet.ejecutar(forzar)
    else:
        raise ValueError(f"modelo desconocido: {nombre}")


def comparativa(log):
    """Tabla con todos los modelos que tengan predicciones en disco, contra el naive."""
    datos = preparacion.preparar_datos()
    y_train, y_val = datos["y_train"], datos["y_val"]

    from tfm_horario.models import elasticnet, ridge   # baratos: no arrastran pmdarima

    etiquetas = {
        "pred_sarima": "SARIMA (sin exog)",
        "pred_sarimax": "SARIMAX (+exog)",
        "pred_ridge": ridge.NOMBRE,
        "pred_elasticnet": elasticnet.NOMBRE,
    }

    predictions_dict = {config.NOMBRE_NAIVE: evaluation.naive_lag24(y_train, y_val)}
    for nombre_art, etiqueta in etiquetas.items():
        if artifacts.existe(nombre_art):
            predictions_dict[etiqueta] = artifacts.cargar(nombre_art)
        else:
            log.warning("falta %s: se omite %s de la comparativa", nombre_art, etiqueta)

    return evaluation.guardar_resultados(y_val, predictions_dict, etiqueta="validation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelos", nargs="+", choices=MODELOS, default=MODELOS,
                        help="cuales entrenar (por defecto, los cuatro)")
    parser.add_argument("--solo-evaluacion", action="store_true",
                        help="no entrena nada: rehace la tabla con las predicciones ya guardadas")
    parser.add_argument("--forzar", action="store_true",
                        help="rehace tratamiento y ordenes en vez de leerlos de artifacts/")
    parser.add_argument("--estrategia-sarima", choices=config.ESTRATEGIAS, default=None)
    parser.add_argument("--estrategia-sarimax", choices=config.ESTRATEGIAS, default=None)
    args = parser.parse_args()

    config.preparar_entorno()
    log = config.configurar_logging("pipeline_horario")
    log.info("modelos=%s forzar=%s salidas=%s", args.modelos, args.forzar, config.OUTPUT_DIR)

    estrategias = {"sarima": args.estrategia_sarima, "sarimax": args.estrategia_sarimax}

    try:
        if not args.solo_evaluacion:
            forzar = args.forzar
            for nombre in args.modelos:
                log.info("=== %s ===", nombre.upper())
                _ejecutar_modelo(nombre, forzar, estrategias)
                # El --forzar solo aplica al primero: a partir de ahi la preparacion
                # ya esta recien regenerada y repetirla serian horas de computo.
                forzar = False
        comparativa(log)
    except Exception:
        log.exception("el pipeline ha fallado")
        return 1

    log.info("pipeline terminado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
