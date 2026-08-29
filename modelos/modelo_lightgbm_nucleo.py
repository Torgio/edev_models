r"""
TFM Energia UCM - LightGBM sobre la matriz compartida "nucleo" (29-ago-2026)

Motivacion: el equipo convergio en `data/gold/matriz_nucleo.parquet` como matriz unica (ver
docs/notas_memoria_tfm.md nota 30) -- ya auditada contra fuga (scripts/auditoria_frontera.py,
"ninguna columna describe el dia objetivo") y ya incluye dos features que propusimos nosotros
(d1_es_puente, d1_regimen_tope_gas). Este script reentrena NUESTRO LightGBM sobre esa base
compartida, para que la fila del leaderboard use la misma matriz que el resto de modelos.

Columnas de entrada = todas menos: control (fecha_pred, fecha_objetivo, ts, split, hora),
objetivo (target_price), "otros" (banderas de trazabilidad: meteo_es_forecast, imputado_apagon,
ventana_pisa_apagon, pbf_publicado_D, pbf_completo_D -- son metadatos de como se construyo la
fila, no informacion disponible para predecir un dia nuevo) y las 7 columnas de bateria con
arranque tardio que el propio catalogo de nucleo marca como excluidas por nulos casi totales.

Hiperparametros: se reutilizan los ganadores de la campaña de Optuna sobre nuestra propia matriz
horaria (300 pruebas, ver afinamiento_lightgbm_horario.py) -- NO se re-afina especificamente para
nucleo en esta primera pasada (distinto catalogo de columnas, los optimos podrian no ser
identicos). Si el modelo se queda como candidato serio, re-afinar sobre nucleo es el siguiente
paso natural, no incluido aqui por tiempo.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/modelo_lightgbm_nucleo.py
"""

import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import optuna
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

REPO = Path(__file__).parent.parent
DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
STUDY_NAME = "lightgbm_horario_precio"
STORAGE = "sqlite:///data_temp/afinamiento_lightgbm_horario.db"

COLS_CONTROL = ["fecha_pred", "fecha_objetivo", "ts", "split", "hora"]
COLS_OTROS = ["meteo_es_forecast", "imputado_apagon", "ventana_pisa_apagon",
              "pbf_publicado_D", "pbf_completo_D"]
COLS_OBJETIVO = "target_price"
COLS_ARRANQUE_TARDIO = [
    "capinst_battery_hybrid_mw", "capinst_solar_pv_hybrid_mw", "capinst_wind_hybrid_mw",
    "ree_cbattery_mw_Dm1", "ree_cbattery_mw_Dm6", "ree_gbattery_mw_Dm1", "ree_gbattery_mw_Dm6",
]


def cargar_nucleo():
    df = pd.read_parquet(REPO / "data" / "gold" / "matriz_nucleo.parquet")
    excluir = set(COLS_CONTROL + COLS_OTROS + COLS_ARRANQUE_TARDIO + [COLS_OBJETIVO])
    feature_cols = [c for c in df.columns if c not in excluir]
    return df, feature_cols


def main():
    DIR_ARTEFACTOS.mkdir(exist_ok=True)

    print("Cargando matriz nucleo...")
    df, feature_cols = cargar_nucleo()
    print(f"  {df.shape[0]} filas, {len(feature_cols)} features de entrada")
    print(f"  reparto: {df['split'].value_counts().to_dict()}")

    train = df[df["split"] == "train"]
    val = df[df["split"] == "validation"]

    print("Leyendo hiperparametros ganadores (Optuna sobre nuestra matriz horaria)...")
    study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
    params = study.best_params
    print(f"  {params}")

    X_train, y_train = train[feature_cols], train[COLS_OBJETIVO]
    X_val, y_val = val[feature_cols], val[COLS_OBJETIVO]

    fijos = {"subsample_freq": 1, "random_state": 42, "n_jobs": -1, "verbosity": -1}
    modelo = LGBMRegressor(**params, **fijos)
    modelo.fit(X_train, y_train)

    pred_val = modelo.predict(X_val)
    mae = mean_absolute_error(y_val, pred_val)
    rmse = mean_squared_error(y_val, pred_val) ** 0.5
    print(f"\nMAE sobre validation (nucleo): {mae:.2f}   RMSE: {rmse:.2f}")
    print("(referencia: MAE 12,86 sobre nuestra propia matriz horaria, tras corregir la fuga de commodities)")

    ruta = DIR_ARTEFACTOS / "lightgbm_nucleo.joblib"
    joblib.dump({
        "modelo": modelo, "feature_cols": feature_cols,
        "hiperparametros": params, "mae_validation": mae, "rmse_validation": rmse,
        "matriz": "nucleo",
    }, ruta)
    print(f"Guardado: {ruta}")

    importancia = pd.DataFrame({
        "feature": feature_cols, "importancia": modelo.feature_importances_,
    }).sort_values("importancia", ascending=False)
    importancia.to_csv(DIR_ARTEFACTOS / "importancia_features_nucleo.csv", index=False)
    print("\nTop 10 features:")
    print(importancia.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
