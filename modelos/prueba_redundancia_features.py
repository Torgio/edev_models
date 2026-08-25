r"""
TFM Energia UCM - Experimento controlado de redundancia de features (25-ago-2026)

Motivacion (ver docs/notas_memoria_tfm.md nota 23 y consulta del equipo sobre limpieza): en vez de
"limpiar a ciegas", se prueba de forma controlada si quitar una columna de cada pareja fuertemente
correlacionada (>0,93) cambia el MAE de validation. Si no cambia, se simplifica el modelo sin
costo; si cambia, la pareja no era tan redundante como parecia.

HALLAZGO PREVIO A LA PRUEBA, IMPORTANTE: al recalcular la matriz de correlacion sobre el dataset
horario actual salieron 76 parejas por encima de 0,93 (no las "4" de un analisis anterior mas
pequeño) -- el dataset crecio bastante desde entonces (10 paises vecinos, PDBC, capacidad). La
mayoria de esas 76 son fisicamente esperables y no candidatas a poda (paises vecinos acoplados
entre si por el mercado europeo, viento a 10m/100m/racha, lags 24h vs 168h de la misma columna).

AVISO (25-ago-2026): los numeros de ESTE script (una sola semilla, random_state=42) resultaron NO
ser fiables -- al repetir con 5 semillas distintas en `prueba_redundancia_robustez.py`, las 4
parejas de aqui abajo mostraron un efecto que CAMBIA DE SIGNO entre semillas (es ruido de muestreo
de columnas de LightGBM, no una señal real). La conclusion que vale es la de ese segundo script,
no la de este: ninguna de las 4 parejas tiene un efecto robusto sobre el MAE, lo cual en realidad
SI confirma la hipotesis original (son seguras de quitar sin coste real) -- solo que hay que
demostrarlo con varias semillas, nunca con una sola corrida.

Las 4 que sí se prueban aqui son las mas claras candidatas a redundancia real -- DOS FUENTES
midiendo/previendo la MISMA magnitud fisica, no dos magnitudes distintas que resultan parecidas:

  1. ree_load        vs  entsoe_load        (demanda real, REE vs ENTSO-E)          corr 0,999
  2. demanda_prev_mw  vs  entsoe_load_forecast_mw   (prevision demanda, ESIOS vs ENTSO-E)  corr 0,994
  3. gen_wind_prev_mw vs  entsoe_wind_forecast_mw   (prevision eolica,  ESIOS vs ENTSO-E)  corr 0,997
  4. gen_solar_pv_prev_mw vs entsoe_solar_forecast_mw (prevision solar, ESIOS vs ENTSO-E)  corr 0,995

NOTA ADICIONAL QUE ESTA PRUEBA DEJA AL DESCUBIERTO: las 3 columnas `entsoe_*_forecast_mw` estan
gateadas detras de `incluir_columnas_pendientes=True` en el dataset DIARIO (docs/columnas_pendientes_equipo.md,
punto 1 -- el equipo decidio dejarlas fuera por defecto) pero se incluyen SIEMPRE, sin gate, en
`construir_dataset_horario.py`. Es una inconsistencia entre los dos scripts que conviene resolver
aparte de esta prueba (ver mensaje al usuario) -- no se corrige aqui para no mezclar los dos temas.

Metodo: mismo LightGBM y mismos hiperparametros ganadores del estudio de Optuna en los 5
entrenamientos (baseline + 4 variantes "sin X"), para que la unica diferencia sea la feature
quitada. Se reporta el MAE de cada variante y el error estandar aproximado de la diferencia de MAE
(bootstrap sobre los errores absolutos de validation) para poder decir si una diferencia es real o
es ruido -- mismo estandar de rigor que aplico el companero de equipo en su ablacion NTC.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/prueba_redundancia_features.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import optuna
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

sys.path.append(str(Path(__file__).parent))
from construir_dataset_horario import construir_dataset_horario, dividir_train_val_test_horario

STUDY_NAME = "lightgbm_horario_precio"
STORAGE = "sqlite:///data_temp/afinamiento_lightgbm_horario.db"
DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
SEMILLA_BOOTSTRAP = 42
N_BOOTSTRAP = 2000

# Cada tupla: (etiqueta, columna_a_quitar_base, con_lag). `con_lag=True` -> es una columna REAL
# que entra lageada (se quitan sus variantes _lag24h/_lag168h). `con_lag=False` -> es una columna
# de prevision "segura" que entra por timestamp exacto, sin lag (se quita tal cual).
PAREJAS = [
    ("sin_entsoe_load (vs ree_load)", "entsoe_load", True),
    ("sin_entsoe_load_forecast_mw (vs demanda_prev_mw)", "entsoe_load_forecast_mw", False),
    ("sin_entsoe_wind_forecast_mw (vs gen_wind_prev_mw)", "entsoe_wind_forecast_mw", False),
    ("sin_entsoe_solar_forecast_mw (vs gen_solar_pv_prev_mw)", "entsoe_solar_forecast_mw", False),
]


def _entrenar_evaluar(X_train, y_train, X_val, y_val, params):
    fijos = {"subsample_freq": 1, "random_state": 42, "n_jobs": -1, "verbosity": -1}
    modelo = LGBMRegressor(**params, **fijos)
    modelo.fit(X_train, y_train)
    pred = modelo.predict(X_val)
    errores_abs = np.abs(y_val.values - pred)
    return mean_absolute_error(y_val, pred), errores_abs


def _bootstrap_se_diferencia(err_base, err_variante, n=N_BOOTSTRAP, semilla=SEMILLA_BOOTSTRAP):
    """Error estandar de MAE_variante - MAE_base via bootstrap pareado sobre las mismas horas."""
    rng = np.random.RandomState(semilla)
    n_horas = len(err_base)
    diffs = np.empty(n)
    for i in range(n):
        idx = rng.randint(0, n_horas, n_horas)
        diffs[i] = err_variante[idx].mean() - err_base[idx].mean()
    return diffs.std()


def main():
    DIR_ARTEFACTOS.mkdir(exist_ok=True)

    print("Leyendo mejores hiperparametros del estudio de Optuna (horario)...")
    study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
    params = study.best_params
    print(f"  MAE de referencia en Optuna: {study.best_value:.2f} EUR/MWh")

    print("\nConstruyendo dataset horario (pdbc='lag')...")
    dataset = construir_dataset_horario(pdbc="lag")
    train, val, _test = dividir_train_val_test_horario(dataset)

    feature_cols = [c for c in dataset.columns if c != "precio"]
    X_train_full, y_train = train[feature_cols], train["precio"]
    X_val_full, y_val = val[feature_cols], val["precio"]

    medianas = X_train_full.median(numeric_only=True)
    X_train_full = X_train_full.select_dtypes(include=[np.number]).fillna(medianas)
    X_val_full = X_val_full[X_train_full.columns].fillna(medianas)

    print(f"\n=== Baseline: {len(X_train_full.columns)} features, "
          f"{len(X_train_full)} horas de train ===")
    mae_base, err_base = _entrenar_evaluar(X_train_full, y_train, X_val_full, y_val, params)
    print(f"MAE baseline (todas las features): {mae_base:.3f} EUR/MWh")

    resultados = [{"variante": "baseline (todas las features)", "columnas_quitadas": "-",
                   "n_features": len(X_train_full.columns), "MAE": round(mae_base, 3),
                   "diff_vs_baseline": 0.0, "SE_bootstrap_diff": 0.0}]

    columnas_a_quitar_todas = []
    for etiqueta, base, con_lag in PAREJAS:
        candidatas = [f"{base}_lag24h", f"{base}_lag168h"] if con_lag else [base]
        cols_pareja = [c for c in candidatas if c in X_train_full.columns]
        if not cols_pareja:
            print(f"\n[aviso] no se encontraron columnas para '{base}', se omite esta pareja.")
            continue
        columnas_a_quitar_todas += cols_pareja

        X_train_v = X_train_full.drop(columns=cols_pareja)
        X_val_v = X_val_full.drop(columns=cols_pareja)
        mae_v, err_v = _entrenar_evaluar(X_train_v, y_train, X_val_v, y_val, params)
        diff = mae_v - mae_base
        se = _bootstrap_se_diferencia(err_base, err_v)
        print(f"\n=== {etiqueta} ===")
        print(f"  columnas quitadas: {cols_pareja}")
        print(f"  MAE: {mae_v:.3f}  (diferencia vs baseline: {diff:+.3f}, "
              f"error estandar de la diferencia: ±{se:.3f})")
        interpretacion = "diferencia DENTRO del ruido -- redundante, se puede quitar" \
            if abs(diff) < 1.5 * se else "diferencia FUERA del ruido -- no era tan redundante"
        print(f"  interpretacion: {interpretacion}")
        resultados.append({"variante": etiqueta, "columnas_quitadas": ", ".join(cols_pareja),
                            "n_features": len(X_train_v.columns), "MAE": round(mae_v, 3),
                            "diff_vs_baseline": round(diff, 3), "SE_bootstrap_diff": round(se, 3)})

    # --- Variante combinada: quitar las 4 columnas duplicadas de ENTSO-E a la vez ---
    if columnas_a_quitar_todas:
        X_train_v = X_train_full.drop(columns=columnas_a_quitar_todas)
        X_val_v = X_val_full.drop(columns=columnas_a_quitar_todas)
        mae_v, err_v = _entrenar_evaluar(X_train_v, y_train, X_val_v, y_val, params)
        diff = mae_v - mae_base
        se = _bootstrap_se_diferencia(err_base, err_v)
        print(f"\n=== combinado: sin las 4 columnas duplicadas de ENTSO-E a la vez ===")
        print(f"  MAE: {mae_v:.3f}  (diferencia vs baseline: {diff:+.3f}, "
              f"error estandar de la diferencia: ±{se:.3f})")
        resultados.append({"variante": "combinado (sin las 4 duplicadas de ENTSO-E)",
                            "columnas_quitadas": ", ".join(columnas_a_quitar_todas),
                            "n_features": len(X_train_v.columns), "MAE": round(mae_v, 3),
                            "diff_vs_baseline": round(diff, 3), "SE_bootstrap_diff": round(se, 3)})

    # --- Variante "inteligente": de las 4 pruebas individuales de arriba, solo 3 columnas
    # mostraron una diferencia real (fuera del ruido) al quitarlas -- de esas, dos MEJORARON el
    # MAE al quitarlas (entsoe_load, entsoe_wind_forecast_mw) y una lo EMPEORO (entsoe_load_
    # forecast_mw, que por tanto se mantiene). En vez de solo probar "todas las 4 juntas" (que
    # mezcla una buena decision con una mala), se prueba la combinacion que junta unicamente las
    # que individualmente ayudaron o fueron neutras -- es la combinacion que de verdad busca una
    # mejora real, no solo simplificar por simplificar.
    cols_inteligente = [c for c in ["entsoe_load_lag24h", "entsoe_load_lag168h",
                                     "entsoe_wind_forecast_mw", "entsoe_solar_forecast_mw"]
                         if c in X_train_full.columns]
    if cols_inteligente:
        X_train_v = X_train_full.drop(columns=cols_inteligente)
        X_val_v = X_val_full.drop(columns=cols_inteligente)
        mae_v, err_v = _entrenar_evaluar(X_train_v, y_train, X_val_v, y_val, params)
        diff = mae_v - mae_base
        se = _bootstrap_se_diferencia(err_base, err_v)
        print(f"\n=== combinacion 'inteligente': quita solo lo que ayudo o fue neutro, "
              f"mantiene entsoe_load_forecast_mw ===")
        print(f"  columnas quitadas: {cols_inteligente}")
        print(f"  MAE: {mae_v:.3f}  (diferencia vs baseline: {diff:+.3f}, "
              f"error estandar de la diferencia: ±{se:.3f})")
        resultados.append({"variante": "inteligente (quita solo lo que ayudo/fue neutro)",
                            "columnas_quitadas": ", ".join(cols_inteligente),
                            "n_features": len(X_train_v.columns), "MAE": round(mae_v, 3),
                            "diff_vs_baseline": round(diff, 3), "SE_bootstrap_diff": round(se, 3)})

    df_resultados = pd.DataFrame(resultados)
    print("\n" + df_resultados.to_string(index=False))
    df_resultados.to_csv(DIR_ARTEFACTOS.parent.parent / "data_temp" / "prueba_redundancia_features.csv",
                          index=False)
    print(f"\nGuardado: data_temp/prueba_redundancia_features.csv")


if __name__ == "__main__":
    main()
