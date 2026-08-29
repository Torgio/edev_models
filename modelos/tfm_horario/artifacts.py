"""Guardado y recuperacion de resultados intermedios.

El SFS y el auto_arima con m=24 son las dos etapas caras del pipeline (horas de
computo). Cachearlas en disco permite relanzar solo la parte que ha cambiado en
vez de repetir todo, que es lo que hacia falta al pasar del notebook (donde el
estado vivia en el kernel) a un script que arranca de cero cada vez.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import joblib

from . import ajustes as config

log = logging.getLogger(__name__)


def ruta(nombre: str) -> Path:
    config.preparar_entorno()
    return config.ARTIFACTS_DIR / f"{nombre}.pkl"


def guardar(obj: Any, nombre: str) -> Path:
    destino = ruta(nombre)
    joblib.dump(obj, destino)
    log.info("artifact guardado -> %s", destino)
    return destino


def cargar(nombre: str) -> Any:
    origen = ruta(nombre)
    if not origen.exists():
        raise FileNotFoundError(f"No existe el artifact {nombre!r} en {origen}. Lanza la etapa que lo genera.")
    log.info("artifact cargado <- %s", origen)
    return joblib.load(origen)


def existe(nombre: str) -> bool:
    return ruta(nombre).exists()


def cachear(nombre: str, fn: Callable[[], Any], forzar: bool = False) -> Any:
    """Devuelve el artifact si ya esta en disco; si no (o con forzar=True), lo calcula."""
    if existe(nombre) and not forzar:
        return cargar(nombre)
    obj = fn()
    guardar(obj, nombre)
    return obj
