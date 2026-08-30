#!/usr/bin/env python3
"""Orquestador: entrena los cuatro modelos y deja los entregables listos para el PR.

Es OPCIONAL. Cada modelo se ejecuta solo y prepara sus propios datos:

    python -m tfm_horario.models.elasticnet
    python -m tfm_horario.models.sarimax --estrategia bloques24

Este script los encadena y, al final, verifica que los entregables cumplen el
formato de Prod.txt antes de que abras el PR:

    python run_pipeline.py                          # los cuatro + verificacion
    python run_pipeline.py --modelos ridge elasticnet
    python run_pipeline.py --solo-verificar         # no entrena, solo repasa entregables/
    python run_pipeline.py --forzar                 # rehace tambien tratamiento y ordenes

NO calcula MAE ni captura de arbitraje: eso lo hace el evaluador central sobre los
12 modelos con un unico script, que es lo que hace los numeros comparables.

La preparacion (carga, split, Spearman + SFS, tratamiento) la comparten todos y se
cachea en `salidas/artifacts/`: la paga el primer modelo que corra y los demas la
reutilizan.
"""

from __future__ import annotations

import argparse
import sys

from tfm_horario import ajustes as config, entrega

MODELOS = ["sarima", "sarimax", "ridge", "elasticnet"]

# Los import de cada modelo van dentro de _ejecutar_modelo porque sarima/sarimax
# arrastran pmdarima y statsmodels: asi una pasada de solo Ridge/ElasticNet no los
# necesita instalados.


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelos", nargs="+", choices=MODELOS, default=MODELOS,
                        help="cuales entrenar (por defecto, los cuatro)")
    parser.add_argument("--solo-verificar", action="store_true",
                        help="no entrena: solo repasa el formato de entregables/")
    parser.add_argument("--forzar", action="store_true",
                        help="rehace tratamiento y ordenes en vez de leerlos de artifacts/")
    parser.add_argument("--estrategia-sarima", choices=config.ESTRATEGIAS, default=None)
    parser.add_argument("--estrategia-sarimax", choices=config.ESTRATEGIAS, default=None)
    args = parser.parse_args()

    config.preparar_entorno()
    log = config.configurar_logging("pipeline_horario")
    log.info("modelos=%s forzar=%s entregables=%s", args.modelos, args.forzar, config.ENTREGABLES_DIR)

    estrategias = {"sarima": args.estrategia_sarima, "sarimax": args.estrategia_sarimax}

    try:
        if not args.solo_verificar:
            forzar = args.forzar
            for nombre in args.modelos:
                log.info("=== %s ===", nombre.upper())
                _ejecutar_modelo(nombre, forzar, estrategias)
                # El --forzar solo aplica al primero: a partir de ahi la preparacion
                # ya esta recien regenerada y repetirla serian horas de computo.
                forzar = False

        repaso = entrega.verificar_entregables()
        log.info("Verificacion de entregables:\n%s", repaso.to_string())
        if not repaso["ok"].all():
            log.error("hay entregables incompletos o con formato incorrecto: NO abras el PR todavia")
            return 1
        log.info("los %d entregables cumplen el formato de Prod.txt", len(repaso))
    except Exception:
        log.exception("el pipeline ha fallado")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
