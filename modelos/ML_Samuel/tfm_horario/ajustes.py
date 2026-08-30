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
# En produccion el codigo vive en:
#
#   edev_models/modelos/                   <- aqui esta construir_dataset_horario.py
#   `- ML_samuel/                          <- BASE_DIR: este proyecto
#      |- run_pipeline.py
#      |- tfm_horario/                     <- este paquete
#      |- entregables/<modelo_id>/         <- lo que se sube al PR
#      `- salidas/                         <- artifacts y logs (NO van al PR)
#
# construir_dataset_horario.py esta en el directorio PADRE, no al lado, asi que no
# vale con anadir el padre del paquete: se busca hacia arriba hasta encontrarlo.
_AQUI = Path(__file__).resolve().parent
BASE_DIR = _AQUI.parent


def _localizar_modelos_dir() -> Path:
    """Directorio que contiene `construir_dataset_horario.py`.

    Se busca subiendo desde este paquete, de modo que la misma configuracion sirva
    tanto si el proyecto esta en `modelos/ML_samuel/` como si algun dia se mueve.
    `TFM_MODELOS_DIR` lo fuerza a mano si hiciera falta.
    """
    forzado = os.environ.get("TFM_MODELOS_DIR")
    if forzado:
        return Path(forzado).resolve()
    for candidato in [BASE_DIR, *BASE_DIR.parents]:
        if (candidato / "construir_dataset_horario.py").exists():
            return candidato
    return BASE_DIR.parent          # fallback: modelos/, aunque aun no este el fichero


MODELOS_DIR = _localizar_modelos_dir()
OUTPUT_DIR = Path(os.environ.get("TFM_OUTPUT_DIR", BASE_DIR / "salidas")).resolve()

# Lo que se entrega en el PR: modelo entrenado + pred_val_2025.csv + metadata.json
ENTREGABLES_DIR = Path(os.environ.get("TFM_ENTREGABLES_DIR", BASE_DIR / "entregables")).resolve()

ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"     # objetos .pkl reutilizables entre etapas
LOGS_DIR = OUTPUT_DIR / "logs"

def preparar_entorno() -> None:
    """Crea los directorios de salida y deja `construir_dataset_horario` importable."""
    for d in (ARTIFACTS_DIR, LOGS_DIR, ENTREGABLES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # MODELOS_DIR: para importar construir_dataset_horario y construir_dataset_maestro.
    # BASE_DIR: para que `import tfm_horario` funcione desde cualquier directorio.
    for d in (str(MODELOS_DIR), str(BASE_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)


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
# FRONTERAS DEL SPLIT -- fijadas por Prod.txt. NO TOCAR.
#   train:      hasta el 31-dic-2024 23:00 UTC inclusive
#   validation: 2025 completo en UTC -> 8760 horas exactas
#   test:       2026 -> SELLADO, una sola apertura el 31-ago-2026
#
# El corte es por TIMESTAMP UTC, no por fecha local Madrid como hacia
# `dividir_train_val_test_horario`. Lo exige el formato de entrega: pred_val_2025.csv
# son "las horas de 2025" en UTC, y cortando por dia local las 23:00 UTC del 31-dic
# caerian en el split del año siguiente y saldrian 8759 u 8761 filas.
# ---------------------------------------------------------------------------
VAL_INICIO_UTC = "2025-01-01 00:00:00+00:00"
VAL_FIN_UTC = "2025-12-31 23:00:00+00:00"     # inclusive
TEST_INICIO_UTC = "2026-01-01 00:00:00+00:00"
HORAS_VAL_2025 = 8760                          # 2025 no es bisiesto

FECHA_APERTURA_TEST = date(2026, 8, 31)

TARGET = "precio"          # unica columna de destino del dataset horario
TZ_LOCAL = "Europe/Madrid"
FREQ = "h"

# Parametros de construccion del dataset (ver construir_dataset_horario)
SOLO_FILAS_VALIDAS = True
PDBC = None                # None | "lag". NUNCA "mismodia_diagnostico": es fuga (ver abajo)

# ---------------------------------------------------------------------------
# IDENTIDAD DEL ENTREGABLE (metadata.json)
# ---------------------------------------------------------------------------
AUTOR = os.environ.get("TFM_AUTOR", "Samuel")
SEMILLA = 42

# modelo_id -> familia, tal y como apareceran en el leaderboard de los 12
MODELOS = {
    "sarima_horario": "estadistico",
    "sarimax_horario": "estadistico",
    "ridge_horario": "lineal",
    "elasticnet_horario": "lineal",
}

# ---------------------------------------------------------------------------
# FILTRO DE FUGA (Prod.txt, aviso 3)
# ---------------------------------------------------------------------------
# Regla: ninguna columna puede referirse al dia objetivo si su fuente publica
# despues de las 11:00 de la vispera (el mercado casa a las 12:00).
#
# En el dataset horario esto afecta a dos cosas:
#
# 1. ERA5 (`COLS_CLIMA`). `construir_dataset_horario` las une por timestamp EXACTO
#    de la hora objetivo, sin desplazar. ERA5 es REANALISIS -- meteorologia
#    reconstruida a posteriori, no un pronostico -- asi que la version del dia
#    objetivo no existia cuando se cerro el mercado. Prod.txt la excluye
#    explicitamente. Sus versiones *_lag24h / *_lag168h SI valen.
#
# 2. Columnas `PELIGRO_mismodia_*` (PDBC del propio D+1). Solo aparecen si se pide
#    pdbc="mismodia_diagnostico", que este pipeline nunca usa, pero se filtran por
#    si alguien cambia PDBC arriba sin leer esto.
#
# Los lags 24h/168h de demanda y precio reales NO son fuga: "con un dia de desfase
# si valen". Y las previsiones day-ahead (esios_forecast_da, entsoe_forecast_da,
# NTC, autoconsumo previsto) se publican antes del cierre, asi que tambien entran.
PREFIJOS_PROHIBIDOS = ("PELIGRO_",)
SUFIJOS_DE_LAG = ("_lag24h", "_lag168h")

# Si no se puede importar COLS_CLIMA de construir_dataset_maestro, se usa esta lista
# como red de seguridad (nombres de era5_weather_agg vistos en el constructor).
CLIMA_FALLBACK = ("t2m_mean", "d2m_mean", "ssrd_mean", "u10_mean", "v10_mean",
                  "wind_speed_mean", "tp_sum", "sp_mean", "tcc_mean")

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
SFS_N_SPLITS = 5
SFS_SCORING = "neg_mean_absolute_error"
SFS_MAX_FILAS = 24 * 365   # SFS sobre el ultimo año de train; None = todo train
SFS_TOL = None             # con un valor (p.ej. 1e-3) el SFS para cuando deja de mejorar

# n_jobs=1 EN EL BOSQUE, no -1. El paralelismo lo pone el SFS (n_jobs=-1 abajo), que
# reparte los folds entre cores. Si ademas cada bosque pide todos los cores, se
# solapan los dos niveles y se pelean por la CPU (oversubscription): en una maquina
# con pocos vCPU, como un VPS, eso hace el ajuste MAS lento, no mas rapido.
# El resultado es identico: con random_state fijo el bosque no depende de n_jobs.
SFS_RF_PARAMS = dict(n_estimators=200, max_depth=8, random_state=42, n_jobs=1)

# --- Palancas para acortar el SFS (es la etapa cara del pipeline) ---------------
# Medido sobre un caso de prueba, respecto a la configuracion de arriba:
#   n_estimators=100 ............ x2.0 mas rapido
#   SFS_N_SPLITS=3 .............. x1.6
#   ambas a la vez .............. x3.2   (misma seleccion en la prueba)
#   SFS_TOL=1e-3 ................ x1.8   (CAMBIA la seleccion: para antes)
# Las tres primeras solo afectan a la precision de la estimacion interna; la ultima
# cambia cuantas features salen, asi que si la usas, dilo en la memoria.

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

# "walkforward" se elimino: horizonte de 1 hora, no es la tarea (el mercado casa las
# 24 horas de D+1 de golpe con informacion hasta D), y tardaba >67 min por año.
ESTRATEGIAS = ("directo", "bloques24")
ESTRATEGIA_SARIMA = "bloques24"    # "directo" | "bloques24"
ESTRATEGIA_SARIMAX = "directo"     # con exogenas conocidas en D, "directo" es legitimo

# ---------------------------------------------------------------------------
# Modelos lineales
# ---------------------------------------------------------------------------
RIDGE_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10, 50, 100, 200, 500]
EN_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10]
EN_L1_RATIOS = [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]   # 1.0 = Lasso puro
EN_MAX_ITER = 10000

