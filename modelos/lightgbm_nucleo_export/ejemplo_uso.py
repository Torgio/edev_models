"""Ejemplo minimo de carga y prediccion -- no depende del resto del repo."""
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
