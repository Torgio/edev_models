r"""
TFM Energia UCM - Exportar el LightGBM sobre nucleo en formato nativo, portable (30-ago-2026)

Motivacion: revisando scripts/predecir.py se confirmo que NO se puede "adaptar" este modelo al
formato keras+json del resto de familias -- LightGBM no es una red de Keras, y su propio codigo
ya lo sabe: la familia "boosting" tiene una clase dedicada (`BosqueHorario` en
scripts/entrenar_finales.py) que carga el modelo nativo de LightGBM (.txt), nunca keras.

PERO tampoco encaja tal cual en `BosqueHorario`: esa clase espera 24 modelos SEPARADOS, uno por
hora, alimentados con la MISMA fila de un dia (arquitectura "una fila = un dia", 24 salidas) --
justo la estructura que nuestro proyecto abandono hace semanas (nota 15) porque un solo modelo
compartiendo todas las horas rinde mejor (lo confirma su propia familia "boosting": 13,3-13,9 de
MAE en test, frente a los 12,92 de este modelo). Forzar el nuestro a 24 piezas separadas
significaria tirar exactamente la mejora que lo hace mejor que el de ellos.

Por eso se exporta aqui en formato NATIVO de LightGBM (.txt, el mismo criterio de longevidad que
ya usa el equipo: "un pickle ata el modelo a la version exacta de la libreria... estos ficheros
los tienen que poder abrir los companeros dentro de un año") pero como UN SOLO modelo, con su
propio metadata.json y un script de ejemplo -- no como un reemplazo de la familia "boosting" en
production/models/, sino como una entrada de comparacion aparte, honesta sobre su propia
arquitectura.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/exportar_lightgbm_nucleo_nativo.py
"""

import json
from pathlib import Path

import joblib

REPO = Path(__file__).parent.parent
DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
OUT_DIR = Path(__file__).parent / "lightgbm_nucleo_export"


def main():
    OUT_DIR.mkdir(exist_ok=True)

    print("Cargando modelo entrenado (modelos/artefactos/lightgbm_nucleo.joblib)...")
    art = joblib.load(DIR_ARTEFACTOS / "lightgbm_nucleo.joblib")
    modelo = art["modelo"]
    feature_cols = art["feature_cols"]

    ruta_txt = OUT_DIR / "modelo.txt"
    modelo.booster_.save_model(str(ruta_txt))
    print(f"Guardado modelo nativo LightGBM: {ruta_txt}")

    metadata = {
        "descripcion": "LightGBM sobre la matriz nucleo -- UN SOLO modelo para las 24 horas "
                       "(hora incluida como feature), no 24 modelos separados como la familia "
                       "'boosting' de production/models/.",
        "matriz": "nucleo",
        "hash_matriz": "4a8f328e",
        "arquitectura": "un_modelo_todas_las_horas",
        "n_features": len(feature_cols),
        "feature_cols_en_orden": feature_cols,
        "objetivo": "absoluto",
        "hiperparametros": art["hiperparametros"],
        "mae_validation": art["mae_validation"],
        "rmse_validation": art["rmse_validation"],
        "como_cargar": (
            "import lightgbm as lgb; modelo = lgb.Booster(model_file='modelo.txt'); "
            "pred = modelo.predict(X[feature_cols_en_orden])  -- X debe traer EXACTAMENTE "
            "estas columnas, en este orden, construidas desde la matriz nucleo."
        ),
    }
    ruta_meta = OUT_DIR / "metadata.json"
    with open(ruta_meta, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"Guardado: {ruta_meta}")

    ejemplo = '''"""Ejemplo minimo de carga y prediccion -- no depende del resto del repo."""
import json
import lightgbm as lgb
import pandas as pd

with open("metadata.json", encoding="utf-8") as f:
    meta = json.load(f)

modelo = lgb.Booster(model_file="modelo.txt")

# X = tu propio DataFrame con las columnas de la matriz nucleo
# X = pd.read_parquet(".../matriz_nucleo.parquet")
X = X[meta["feature_cols_en_orden"]]
pred = modelo.predict(X)
'''
    (OUT_DIR / "ejemplo_uso.py").write_text(ejemplo, encoding="utf-8")
    print(f"Guardado: {OUT_DIR / 'ejemplo_uso.py'}")
    print(f"\nCarpeta lista para compartir: {OUT_DIR}")


if __name__ == "__main__":
    main()
