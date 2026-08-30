r"""
TFM Energia UCM - Robustez de la prueba de redundancia frente a la semilla (25-ago-2026)

Motivacion: al repetir `prueba_redundancia_features.py` tras anadir una columna nueva sin relacion
(regimen_tope_gas), el signo de la diferencia de MAE se invirtio para 3 de las 4 parejas -- con
colsample_bytree < 1, LightGBM muestrea un subconjunto ALEATORIO de columnas en cada arbol, y el
numero total de columnas del dataset cambia que columnas exactas caen en esa muestra incluso con
la misma semilla. Conclusion: un solo entrenamiento con una sola semilla no es suficiente para
fiarse de diferencias tan pequeñas (0,02-0,12 EUR/MWh sobre un MAE de ~12,7) -- hay que repetir con
varias semillas y mirar si el efecto se mantiene en la MISMA direccion, no solo si supera el error
estandar de un solo entrenamiento.

Este script repite baseline + las 4 parejas individuales con 5 semillas distintas (mismo split de
datos, solo cambia random_state del modelo) y reporta media +- desviacion de la diferencia de MAE
frente al baseline en cada semilla.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/prueba_redundancia_robustez.py
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
SEMILLAS = [42, 1, 7, 123, 2026]

PAREJAS = [
    ("sin_entsoe_load", ["entsoe_load_lag24h", "entsoe_load_lag168h"]),
    ("sin_entsoe_load_forecast_mw", ["entsoe_load_forecast_mw"]),
    ("sin_entsoe_wind_forecast_mw", ["entsoe_wind_forecast_mw"]),
    ("sin_entsoe_solar_forecast_mw", ["entsoe_solar_forecast_mw"]),
]


def _mae_con_semilla(X_train, y_train, X_val, y_val, params, semilla):
    fijos = {"subsample_freq": 1, "random_state": semilla, "n_jobs": -1, "verbosity": -1}
    modelo = LGBMRegressor(**params, **fijos)
    modelo.fit(X_train, y_train)
    return mean_absolute_error(y_val, modelo.predict(X_val))


def main():
    print("Leyendo mejores hiperparametros del estudio de Optuna (horario)...")
    study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
    params = study.best_params

    print("Construyendo dataset horario (pdbc='lag')...")
    dataset = construir_dataset_horario(pdbc="lag")
    train, val, _test = dividir_train_val_test_horario(dataset)

    feature_cols = [c for c in dataset.columns if c != "precio"]
    X_train_full, y_train = train[feature_cols], train["precio"]
    X_val_full, y_val = val[feature_cols], val["precio"]

    medianas = X_train_full.median(numeric_only=True)
    X_train_full = X_train_full.select_dtypes(include=[np.number]).fillna(medianas)
    X_val_full = X_val_full[X_train_full.columns].fillna(medianas)

    filas = []
    for semilla in SEMILLAS:
        print(f"\n--- semilla {semilla} ---")
        mae_base = _mae_con_semilla(X_train_full, y_train, X_val_full, y_val, params, semilla)
        print(f"  baseline: {mae_base:.3f}")
        fila = {"semilla": semilla, "MAE_baseline": round(mae_base, 3)}
        for etiqueta, cols in PAREJAS:
            cols_ok = [c for c in cols if c in X_train_full.columns]
            X_train_v = X_train_full.drop(columns=cols_ok)
            X_val_v = X_val_full.drop(columns=cols_ok)
            mae_v = _mae_con_semilla(X_train_v, y_train, X_val_v, y_val, params, semilla)
            diff = mae_v - mae_base
            print(f"  {etiqueta}: {mae_v:.3f}  (diff {diff:+.3f})")
            fila[f"diff_{etiqueta}"] = round(diff, 3)
        filas.append(fila)

    df = pd.DataFrame(filas)
    print("\n=== Resultado por semilla ===")
    print(df.to_string(index=False))

    print("\n=== Resumen: media +- desviacion de la diferencia entre semillas ===")
    for etiqueta, _ in PAREJAS:
        col = f"diff_{etiqueta}"
        media, sd = df[col].mean(), df[col].std()
        mismo_signo = (df[col] > 0).all() or (df[col] < 0).all()
        veredicto = "efecto CONSISTENTE (mismo signo en las 5 semillas)" if mismo_signo \
            else "efecto INCONSISTENTE (cambia de signo entre semillas -- es ruido, no una senal real)"
        print(f"  {etiqueta}: {media:+.3f} +- {sd:.3f}   -> {veredicto}")

    ruta = Path(__file__).parent.parent / "data_temp" / "prueba_redundancia_robustez.csv"
    df.to_csv(ruta, index=False)
    print(f"\nGuardado: {ruta}")


if __name__ == "__main__":
    main()
