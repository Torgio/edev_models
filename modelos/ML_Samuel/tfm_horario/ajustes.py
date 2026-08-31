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


def _localizar(relativo: str, variable: str) -> Path:
    """Busca `relativo` subiendo desde este proyecto. `variable` lo fuerza a mano."""
    forzado = os.environ.get(variable)
    if forzado:
        return Path(forzado).resolve()
    for candidato in [BASE_DIR, *BASE_DIR.parents]:
        if (candidato / relativo).exists():
            return (candidato / relativo).resolve()
    return (BASE_DIR.parent.parent / relativo).resolve()   # edev_models/<relativo>


# La entrada YA NO es la BBDD: es la matriz depurada del equipo
# (construir_matriz.py + depurar_matriz.py + auditoria_frontera.py).
# 133 columnas, 0 nulos, apagon de abril-2025 imputado y marcado.
RUTA_MATRIZ = _localizar("data/gold/matriz_nucleo.csv", "TFM_RUTA_MATRIZ")
OUTPUT_DIR = Path(os.environ.get("TFM_OUTPUT_DIR", BASE_DIR / "salidas")).resolve()
ENTREGABLES_DIR = Path(os.environ.get("TFM_ENTREGABLES_DIR", BASE_DIR / "entregables")).resolve()

ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"     # objetos .pkl reutilizables entre etapas
LOGS_DIR = OUTPUT_DIR / "logs"

def preparar_entorno() -> None:
    """Crea los directorios de salida y deja `construir_dataset_horario` importable."""
    for d in (ARTIFACTS_DIR, LOGS_DIR, ENTREGABLES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Para que `import tfm_horario` funcione desde cualquier directorio.
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))


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

TARGET = "target_price"    # objetivo en matriz_nucleo.csv
COL_TIMESTAMP = "ts"       # hora objetivo, en UTC
TZ_LOCAL = "Europe/Madrid"
FREQ = "h"

# Columnas de control de la matriz: identifican la fila, no son features.
# `hora` es la excepcion: es control Y feature (el perfil horario del precio).
COLUMNAS_CONTROL = ("fecha_pred", "fecha_objetivo", "ts", "split")
COLUMNAS_ESPERADAS = 133   # si no cuadra, la matriz ha cambiado: revisar antes de entrenar

# Huecos en la rejilla horaria UTC que se toleran e interpolan. Los cambios de hora
# de marzo y octubre dejan 1-2 huecos si la matriz se construye por dia local x 24
# slots. Mas que esto no es el DST: es una matriz incompleta, y el pipeline para.
MAX_HORAS_A_RELLENAR = 48

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
# FRONTERA DE INFORMACION (Prod.txt, aviso 3)
# ---------------------------------------------------------------------------
# Regla: ninguna columna puede referirse al dia OBJETIVO si su fuente publica
# despues de las 11:00 de la vispera (el mercado casa a las 12:00).
#
# En matriz_nucleo.csv el sufijo indica el dia de la variable RESPECTO AL DIA EN
# QUE SE PREDICE (`fecha_pred`), no respecto al objetivo:
#
#   *_D     -> dia D, el dia en que predices. Ya ha ocurrido cuando cierras el
#              mercado de D+1, asi que NO es fuga.
#   *_Dm1, *_Dm2, *_Dm6 -> mas atras todavia.
#   d1_*    -> calendario del dia objetivo (D+1): se conoce con certeza siempre.
#   *_meteo -> prevision ECMWF para el dia objetivo (ver DUDOSAS abajo).
#
# Con esa convencion, `pdbc_*_D` y `pbfli_*_D` NO son "PBF del dia objetivo": son
# del dia D, publicados a las 13:45 de D-1. El equipo ya paso `auditoria_frontera.py`
# sobre la matriz, asi que por defecto no se descarta ninguna columna.
#
# Si al revisarlo resultara que el sufijo _D significa el dia objetivo, basta con
# anadir los bloques a COLUMNAS_PROHIBIDAS y relanzar con --forzar.
COLUMNAS_PROHIBIDAS: tuple[str, ...] = ()
PREFIJOS_PROHIBIDOS: tuple[str, ...] = ()

# --- Features a declarar en metadata.json["features_dudosas"] ------------------
# No se descartan; se declaran para que el revisor las mire. Dos familias:
#
# 1. `*_meteo` (6 columnas): prevision meteorologica del dia objetivo. Es legitima
#    (una prevision se publica antes del cierre), PERO Nucleo.txt avisa de que solo
#    es prevision ECMWF real desde 2024-04; antes es "pseudo-prevision". Como train
#    llega hasta 2024-12 y validation es 2025 entero, la mayor parte del train usa
#    pseudo-prevision y TODO validation usa prevision real. Son dos cosas distintas
#    bajo el mismo nombre de columna. La bandera `meteo_es_forecast` dice cual es.
#
# 2. `pbf_publicado_D` / `pbf_completo_D`: testigos de si el PBF de D estaba
#    publicado. No llevan valores del PBF, pero dependen de su publicacion.
SUFIJOS_DUDOSOS = ("_meteo",)
DUDOSAS_EXPLICITAS = ("pbf_publicado_D", "pbf_completo_D", "meteo_es_forecast")

# ---------------------------------------------------------------------------
# Seleccion de features
# ---------------------------------------------------------------------------
# Cuatro modos, elegibles con --seleccion en cualquier modelo:
#
#   "ambos"    Spearman y despues SFS sobre los supervivientes. Por defecto.
#   "spearman" solo el filtro de correlacion. Barato (segundos) y deja ~119 features.
#   "sfs"      solo seleccion secuencial, sobre las 128 features de la matriz. Es el
#              modo MAS CARO con diferencia: sin el pre-filtro de Spearman, el SFS
#              arranca con 128 candidatos en vez de 119, y ninguno se ha descartado
#              antes por redundancia.
#   "ninguna"  sin seleccion: los modelos reciben las 128 features. Util como
#              referencia -- dice cuanto aporta realmente seleccionar.
#
# Cada modo cachea sus artifacts por separado, asi que se pueden lanzar los cuatro
# sin que se pisen, y el modelo_id del entregable lleva el sufijo del modo.
MODOS_SELECCION = ("ambos", "spearman", "sfs", "ninguna")

# Por defecto "spearman": es el pipeline basico acordado. Quita las features sin
# relacion monotona con el precio y las redundantes entre si, sin pagar el SFS, asi
# que sigue arrancando en segundos. Ademas evita el peor condicionamiento numerico
# de "ninguna" (128 features con familias casi identicas), que es donde ElasticNet
# daba ConvergenceWarning.
# Los demas modos se piden con --seleccion y se comparan contra esta referencia.
MODO_SELECCION = "spearman"
# Protegidas: la relacion precio/hora del dia es en U, no monotona, y Spearman le da
# un rho casi cero. Sin esta proteccion el selector tiraria la feature mas importante.
FEATURES_PROTEGIDAS = ("hora", "hora_sin", "hora_cos",
                       "d1_dow", "d1_month", "d1_is_weekend", "d1_is_festivo")

SPEARMAN_TARGET_THRESHOLD = 0.10
# 0.85 en vez de 0.90: la matriz trae familias muy redundantes entre si (16 columnas
# de PDBC, 14 de PBF, 9 de capacidad). Apretar el filtro de colinealidad es la forma
# mas barata de acortar el SFS, porque reduce los candidatos ANTES de la parte cara.
SPEARMAN_COLLINEARITY_THRESHOLD = 0.85
SPEARMAN_P_VALUE_MAX = 0.05
SPEARMAN_MIN_OVERLAP = 30

# CAMBIO IMPORTANTE al pasar a matriz_nucleo.csv: la matriz trae 128 features y
# ~119 sobreviven a Spearman, muchas mas que el dataset anterior. Con "auto"
# (= la mitad, 60) el SFS forward son 5.370 evaluaciones x n_splits = mas de 16.000
# ajustes de random forest: dias de computo en un VPS, no horas.
# Con 25 baja a 2.675 evaluaciones (x3 mas barato) y sigue siendo mas features de
# las que estos cuatro modelos necesitan. Subelo si tienes computo de sobra.
SFS_N_FEATURES = 25        # "auto" (= la mitad) o un entero
SFS_DIRECTION = "forward"
SFS_N_SPLITS = 3           # 3 en vez de 5: con 119 candidatos, 5 folds no compensa
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
# Ya no hay parametros de imputacion (GAP_LIMIT_HORAS, MISSING_PCT_ALTO,
# UMBRAL_CORR_DROP): matriz_nucleo.csv llega con 0 nulos y el apagon de abril-2025
# imputado y marcado por depurar_matriz.py. Si aparece un NaN, el pipeline PARA en
# vez de taparlo -- significa que algo cambio aguas arriba.

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
# El ajuste de statsmodels guarda las matrices del filtro de Kalman para CADA hora:
# nobs x k_states^2. Con m=24 (k_states ~28) y 5 años de historia son ~5 GB, y el
# OOM killer se lleva el proceso ("Killed", sin traceback). Con low_memory=True solo
# se conserva lo imprescindible: medido, 1.034 MB -> 152 MB sobre un año, y
# `forecast` y `append(refit=False)` -- lo unico que necesita bloques24 -- siguen
# funcionando. A cambio se pierden diagnosticos que este pipeline no usa (residuos
# suavizados, estados suavizados).
SARIMA_LOW_MEMORY = True

# Historia maxima para ajustar SARIMA/SARIMAX. None = toda. El consumo crece lineal
# con las horas, asi que recortar es la otra palanca si aun asi no cabe: 3 años de
# precio horario ya contienen de sobra la estructura diaria y semanal, y ademas
# dejan fuera la crisis del gas de 2021-2022, que se comporta muy distinto de 2025.
SARIMA_MAX_HORAS_TRAIN = None      # p.ej. 24 * 365 * 3

# Horas finales de train sobre las que se reconstruye el ESTADO del filtro de Kalman
# despues de estimar los parametros. `low_memory=True` estima sin guardar el filtro,
# pero entonces no hay estado y `extend()` no puede continuar la serie; re-filtrar
# solo esta ventana lo recupera con memoria acotada.
# El estado de un modelo con m=24 converge en pocos dias, asi que 30 dias sobran:
# medido, la prediccion sale IDENTICA a filtrar los 5 años enteros (2.042 MB -> 190 MB).
# None = filtrar todo train (exacto por construccion, pero es lo que provocaba el OOM).
SARIMA_VENTANA_ESTADO = 24 * 30

ORDER_FALLBACK = (2, 0, 1)
SEASONAL_ORDER_FALLBACK = (1, 1, 1, M_ESTACIONAL)

# "walkforward" se elimino: horizonte de 1 hora, no es la tarea (el mercado casa las
# 24 horas de D+1 de golpe con informacion hasta D), y tardaba >67 min por año.
ESTRATEGIAS = ("directo", "bloques24")
# Los DOS en "bloques24": es la simulacion fiel del mercado diario. Cada dia se
# predicen las 24 horas de D+1 con informacion hasta el final de D, que es
# exactamente lo que tienes al casar a las 12:00.
#
# En SARIMAX estuvo en "directo" un tiempo. No era fuga -- al reves: con "directo"
# el estado del ARIMA se congela en dic-2024 y para diciembre-2025 lleva doce meses
# sin refrescarse, o sea MENOS informacion de la que da la realidad. Las exogenas
# traen los lags de precio, asi que la diferencia es menor que en SARIMA, pero la
# memoria de residuos del modelo si se queda obsoleta.
#
# Ademas, asi las predicciones son comparables con las de los otros 11 modelos del
# leaderboard: un LightGBM con features lag_24h dispone del mismo conjunto de
# informacion que "bloques24".
ESTRATEGIA_SARIMA = "bloques24"    # "directo" | "bloques24"
ESTRATEGIA_SARIMAX = "bloques24"

# "directo" sigue disponible con --estrategia y es util como contraste: no toca
# ningun precio real de 2025, asi que da una cota inferior de lo que el modelo
# sabe hacer sin refrescar el estado.

# ---------------------------------------------------------------------------
# Modelos lineales
# ---------------------------------------------------------------------------
RIDGE_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10, 50, 100, 200, 500]
EN_ALPHAS = [0.001, 0.01, 0.1, 1, 5, 10]
EN_L1_RATIOS = [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]   # 1.0 = Lasso puro
# 100.000, no 10.000. Con la matriz completa (modo "ninguna", 128 features con
# familias casi identicas) el descenso por coordenadas necesita ~43.000 iteraciones
# para los alphas mas pequeños; con el tope antiguo se quedaba a 4x de la tolerancia
# y devolvia coeficientes a medio ajustar sin que nadie se enterara.
EN_MAX_ITER = 100000

