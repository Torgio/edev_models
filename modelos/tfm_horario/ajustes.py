"""Ajustes del pipeline horario: rutas, fechas del split e hiperparametros.

Se llama `ajustes.py` y no `config.py` para no confundirlo con el `config.py` que
ya hay en /home/ubuntu/scripts/modelos/, que es el cargador de credenciales
(`load_config`, `load_cds_key`). Son cosas distintas y no se pisan: este paquete
no toca la BBDD, de eso se encarga `construir_dataset_horario` con AQUEL config.
Dentro del paquete se importa como `from . import ajustes as config`, asi que en
el codigo se sigue leyendo `config.TRAIN_END_TFM`.

Todo lo que en el notebook estaba hardcodeado (rutas de Windows, umbrales, fechas)
vive aqui. En el servidor se sobreescribe con variables de entorno, sin tocar codigo:

    export TFM_MODELOS_DIR=/opt/edev_models/modelos
    export TFM_OUTPUT_DIR=/var/lib/tfm/salidas
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
# Sustituye al sys.path.append("C:/Users/Powan/Desktop/...") del notebook.
#
# Por defecto MODELOS_DIR es el directorio PADRE de este paquete, que es justo
# donde vive `construir_dataset_horario.py`. En el servidor:
#
#   /home/ubuntu/scripts/modelos/          <- MODELOS_DIR (deducido solo)
#   |- construir_dataset_horario.py
#   |- bronzeDF_pipeline.py
#   |- run_pipeline.py
#   |- tfm_horario/                        <- este paquete
#   `- salidas/                            <- OUTPUT_DIR (se crea solo)
#
# Con esa estructura NO hace falta exportar ninguna variable de entorno. Las dos
# de abajo estan solo por si algun dia quieres separar codigo y salidas (por
# ejemplo, escribir en /var/lib/tfm en vez de dentro del repo).
_AQUI = Path(__file__).resolve().parent

MODELOS_DIR = Path(os.environ.get("TFM_MODELOS_DIR", _AQUI.parent)).resolve()
OUTPUT_DIR = Path(os.environ.get("TFM_OUTPUT_DIR", _AQUI.parent / "salidas")).resolve()

ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"     # objetos .pkl reutilizables entre etapas
FIGURES_DIR = OUTPUT_DIR / "figuras"
PREDICTIONS_DIR = OUTPUT_DIR / "predicciones"
METRICS_DIR = OUTPUT_DIR / "metricas"
LOGS_DIR = OUTPUT_DIR / "logs"


def preparar_entorno() -> None:
    """Crea los directorios de salida y deja `construir_dataset_horario` importable."""
    for d in (ARTIFACTS_DIR, FIGURES_DIR, PREDICTIONS_DIR, METRICS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if str(MODELOS_DIR) not in sys.path:
        sys.path.insert(0, str(MODELOS_DIR))


def configurar_logging(nombre: str = "pipeline", nivel: int = logging.INFO) -> logging.Logger:
    """Log a stdout y a fichero. En el servidor sustituye a los print() del notebook."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOGS_DIR / f"{nombre}.log", encoding="utf-8"),
        ],
        force=True,
    )
    return logging.getLogger(nombre)


# ---------------------------------------------------------------------------
# FRONTERAS DEL SPLIT -- fijadas por la instruccion del 27-ago-2026. NO TOCAR.
#   train:      hasta el 31-dic-2024 inclusive
#   validation: 2025 completo
#   test:       2026 -> SELLADO hasta el 31-ago-2026, una sola apertura
# Se definen AQUI y no se usa `dividir_train_val_test_horario`, que hereda
# TRAIN_END/VAL_END de construir_dataset_maestro (compartidas con el dataset diario):
# si alguien cambia alli las fechas, este pipeline no se entera y entrenamos con 2026.
# ---------------------------------------------------------------------------
TRAIN_END_TFM = date(2024, 12, 31)
VAL_END_TFM = date(2025, 12, 31)
FECHA_APERTURA_TEST = date(2026, 8, 31)

TARGET = "precio"          # unica columna de destino del dataset horario
TZ_LOCAL = "Europe/Madrid"
FREQ = "h"

# Parametros de construccion del dataset (ver construir_dataset_horario)
SOLO_FILAS_VALIDAS = True
PDBC = None                # None | "lag" | "mismodia_diagnostico" (este ultimo SOLO diagnostico)

# ---------------------------------------------------------------------------
# Seleccion de features
# ---------------------------------------------------------------------------
# Protegidas: la relacion precio/hora del dia es en U, no monotona, y Spearman le da
# un rho casi cero. Sin esta proteccion el selector tiraria la feature mas importante.
FEATURES_PROTEGIDAS = ("hora", "dow", "month", "is_weekend")

SPEARMAN_TARGET_THRESHOLD = 0.10
SPEARMAN_COLLINEARITY_THRESHOLD = 0.90
SPEARMAN_P_VALUE_MAX = 0.05
SPEARMAN_MIN_OVERLAP = 30

SFS_N_FEATURES = "auto"    # "auto" o un entero
SFS_DIRECTION = "forward"
SFS_N_SPLITS = 3
SFS_SCORING = "neg_mean_absolute_error"
SFS_MAX_FILAS = 24 * 365   # SFS sobre el ultimo año de train; None = todo train
SFS_RF_PARAMS = dict(n_estimators=100, max_depth=8, random_state=42, n_jobs=-1)

# ---------------------------------------------------------------------------
# Tratamiento de datos
# ---------------------------------------------------------------------------
GAP_LIMIT_HORAS = 6        # huecos de hasta 6h se interpolan; mas largos quedan NaN
MISSING_PCT_ALTO = 0.15
UMBRAL_CORR_DROP = 0.10

# ---------------------------------------------------------------------------
# SARIMA / SARIMAX
# ---------------------------------------------------------------------------
M_ESTACIONAL = 24          # ciclo diario (no 7: eso era el dataset diario)
VENTANA_ORDEN = 24 * 120   # 120 dias para la busqueda de orden con auto_arima
AUTO_ARIMA_PARAMS = dict(
    seasonal=True, m=M_ESTACIONAL, d=None, D=None,
    max_p=3, max_q=3, max_P=1, max_Q=1,
    stepwise=True, trace=True, error_action="ignore", suppress_warnings=True,
)
# Fallback si auto_arima no termina en un tiempo razonable (dejar constancia en la memoria)
ORDER_FALLBACK = (2, 0, 1)
SEASONAL_ORDER_FALLBACK = (1, 1, 1, M_ESTACIONAL)

ESTRATEGIAS = ("walkforward", "directo", "bloques24")
ESTRATEGIA_SARIMA = "bloques24"    # "walkforward" | "directo" | "bloques24"
ESTRATEGIA_SARIMAX = "directo"     # idem

# ---------------------------------------------------------------------------
# Modelos lineales
# ---------------------------------------------------------------------------
RIDGE_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10, 50, 100, 200, 500]
EN_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10]
EN_L1_RATIOS = [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]   # 1.0 = Lasso puro
EN_MAX_ITER = 10000

NOMBRE_NAIVE = "Naive (t-24)"
ZOOM_HORAS_PLOT = 24 * 14
