"""
TFM Energia UCM - F11: ML clasico (XGBoost / LightGBM) contra el liston de baselines.

Entrena 24 modelos por algoritmo (uno por hora del dia, price_h00..price_h23), sobre las mismas
columnas que ya trae `dataset_maestro.csv` -- todas ya construidas con el corte D-1 12:00 respetado
(features "_prev" y "_lag1d"/"_lag7d"), asi que no hace falta desplazar nada aqui: la fila "fecha"
ya es (features conocidas antes del cierre) -> (precio real de ese mismo dia).

Split: se usa la columna "split" que ya trae el CSV (train / validation / test), fijada por
`construir_dataset_maestro.dividir_train_val_test` -- el mismo split que usaron los baselines de F11.

Evaluacion: MISMAS funciones que `notebooks/F11_baselines.ipynb` (errores() e ingreso_arbitraje()),
copiadas literalmente aqui, para que MAE y % de arbitraje capturado sean comparables punto por punto
contra el liston ya fijado:
    - MAE < 19.94 EUR/MWh   (persistencia_d1, el mejor baseline por error)
    - captura > 91.2 %       (media_movil_7d, el mejor baseline por dinero)

Uso:
    cd modelos/
    python entrenar_ml_clasico.py

Requiere: pandas, numpy, scikit-learn, xgboost, lightgbm (pip install -r requirements.txt)
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TRAIN_END = pd.Timestamp("2024-12-31")
VAL_END = pd.Timestamp("2025-12-31")

TARGET_COLS = [f"price_h{h:02d}" for h in range(24)]
NON_FEATURE_COLS = TARGET_COLS + ["split"]

# Listón fijado en F11 baselines (validación 2025).
LISTON_MAE = 19.94        # persistencia_d1
LISTON_CAPTURA = 91.2     # media_movil_7d


# ────────────────────────────────────────────────────────────────────────────
# Evaluación -- copiada tal cual de notebooks/F11_baselines.ipynb para que sea
# la misma vara de medir que se usó con los baselines.
# ────────────────────────────────────────────────────────────────────────────
def tramo(idx: pd.DatetimeIndex) -> pd.Series:
    etiquetas = np.select(
        [idx <= TRAIN_END, idx <= VAL_END],
        ["train", "val"],
        default="test",
    )
    return pd.Series(etiquetas, index=idx)


def errores(pred: pd.DataFrame, real: pd.DataFrame) -> pd.DataFrame:
    comun = pred.dropna(how="all").index.intersection(real.index)
    diferencia = pred.loc[comun] - real.loc[comun]
    etiquetas = tramo(comun)

    filas = []
    for nombre_tramo in ["train", "val", "test"]:
        sub = diferencia[etiquetas == nombre_tramo]
        if sub.empty:
            continue
        valores = sub.stack()
        filas.append({
            "tramo": nombre_tramo,
            "dias": len(sub),
            "MAE": valores.abs().mean(),
            "RMSE": np.sqrt((valores ** 2).mean()),
            "sesgo": valores.mean(),
        })
    return pd.DataFrame(filas).set_index("tramo")


def ingreso_arbitraje(pred: pd.DataFrame, real: pd.DataFrame, k: int = 4, eficiencia: float = 0.85) -> pd.Series:
    comun = pred.dropna(how="all").index.intersection(real.index)
    p = pred.loc[comun].to_numpy()
    r = real.loc[comun].to_numpy()

    orden = np.argsort(p, axis=1)
    horas_carga = orden[:, :k]
    horas_descarga = orden[:, -k:]

    coste = np.take_along_axis(r, horas_carga, axis=1).sum(axis=1)
    ingreso = np.take_along_axis(r, horas_descarga, axis=1).sum(axis=1) * eficiencia

    return pd.Series(ingreso - coste, index=comun, name="ingreso")


def evaluar_dinero(pred: pd.DataFrame, real: pd.DataFrame, k: int = 4, eficiencia: float = 0.85) -> pd.DataFrame:
    ingreso = ingreso_arbitraje(pred, real, k, eficiencia)
    perfecto = ingreso_arbitraje(real, real, k, eficiencia)

    comun = ingreso.index.intersection(perfecto.index)
    ingreso, perfecto = ingreso.loc[comun], perfecto.loc[comun]
    etiquetas = tramo(comun)

    filas = []
    for nombre_tramo in ["train", "val", "test"]:
        mascara = etiquetas == nombre_tramo
        if not mascara.any():
            continue
        filas.append({
            "tramo": nombre_tramo,
            "dias": int(mascara.sum()),
            "ingreso_dia": ingreso[mascara].mean(),
            "techo_dia": perfecto[mascara].mean(),
            "capturado_%": 100 * ingreso[mascara].sum() / perfecto[mascara].sum(),
            "dias_en_perdida_%": 100 * (ingreso[mascara] < 0).mean(),
        })
    return pd.DataFrame(filas).set_index("tramo")


# ────────────────────────────────────────────────────────────────────────────
# Entrenamiento -- 24 modelos por algoritmo, uno por hora.
# ────────────────────────────────────────────────────────────────────────────
def cargar_dataset(ruta: Path) -> pd.DataFrame:
    df = pd.read_csv(ruta, index_col="fecha", parse_dates=["fecha"])
    return df


def columnas_feature(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    # Columnas constantes (siempre 0, ej. solar de madrugada) -- no aportan, fuera.
    constantes = [c for c in cols if df[c].nunique(dropna=True) <= 1]
    if constantes:
        print(f"  (excluidas {len(constantes)} columnas constantes: {constantes})")
    return [c for c in cols if c not in constantes]


def entrenar_por_hora(modelo_cls, kwargs: dict, X_train, y_train, X_val, feature_cols):
    """Entrena un modelo independiente por cada hora del dia (24 en total)."""
    preds_val = {}
    importancias = pd.DataFrame(index=feature_cols)

    for h in range(24):
        col = f"price_h{h:02d}"
        modelo = modelo_cls(**kwargs)
        modelo.fit(X_train, y_train[col])
        preds_val[col] = modelo.predict(X_val)
        if hasattr(modelo, "feature_importances_"):
            importancias[f"h{h:02d}"] = modelo.feature_importances_

    pred_df = pd.DataFrame(preds_val, index=X_val.index)[TARGET_COLS]
    return pred_df, importancias


def resumen_importancia(importancias: pd.DataFrame, top_n: int = 15) -> pd.Series:
    media = importancias.mean(axis=1).sort_values(ascending=False)
    return media.head(top_n)


def reportar(nombre: str, pred_val: pd.DataFrame, real: pd.DataFrame):
    err = errores(pred_val, real)
    dinero = evaluar_dinero(pred_val, real)

    mae_val = err.loc["val", "MAE"] if "val" in err.index else float("nan")
    captura_val = dinero.loc["val", "capturado_%"] if "val" in dinero.index else float("nan")

    print(f"\n{'=' * 60}\n{nombre}\n{'=' * 60}")
    print(err.round(2).to_string())
    print()
    print(dinero.round(2).to_string())

    beat_mae = mae_val < LISTON_MAE
    beat_dinero = captura_val > LISTON_CAPTURA
    print(f"\n  MAE val:      {mae_val:.2f}  (listón 19.94)   -> {'SUPERA' if beat_mae else 'NO supera'}")
    print(f"  Captura val:  {captura_val:.1f}%  (listón 91.2%)  -> {'SUPERA' if beat_dinero else 'NO supera'}")

    return {"modelo": nombre, "MAE_val": mae_val, "captura_val_%": captura_val,
            "supera_MAE": beat_mae, "supera_dinero": beat_dinero}


def main():
    ruta_csv = Path(__file__).parent / "dataset_maestro.csv"
    print(f"Cargando {ruta_csv} ...")
    df = cargar_dataset(ruta_csv)
    print(f"  {df.shape[0]} dias x {df.shape[1]} columnas")

    feature_cols = columnas_feature(df)
    print(f"  {len(feature_cols)} columnas de features tras excluir target/split/constantes")

    train = df[df["split"] == "train"]
    val = df[df["split"] == "validation"]
    print(f"  train: {len(train)} dias | validation: {len(val)} dias")

    X_train = train[feature_cols].fillna(train[feature_cols].median())
    X_val = val[feature_cols].fillna(train[feature_cols].median())
    y_train = train[TARGET_COLS]
    real_val = val[TARGET_COLS]

    resultados = []

    # ── XGBoost ──────────────────────────────────────────────────────────
    from xgboost import XGBRegressor
    print("\nEntrenando XGBoost (24 modelos, uno por hora)...")
    pred_xgb, imp_xgb = entrenar_por_hora(
        XGBRegressor,
        dict(n_estimators=600, learning_rate=0.03, max_depth=5,
             subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1),
        X_train, y_train, X_val, feature_cols,
    )
    resultados.append(reportar("XGBoost (24 modelos/hora)", pred_xgb, real_val))
    print("\nTop 15 features (importancia media entre las 24 horas) -- XGBoost:")
    print(resumen_importancia(imp_xgb).round(4).to_string())

    # ── LightGBM ─────────────────────────────────────────────────────────
    from lightgbm import LGBMRegressor
    print("\nEntrenando LightGBM (24 modelos, uno por hora)...")
    pred_lgb, imp_lgb = entrenar_por_hora(
        LGBMRegressor,
        dict(n_estimators=800, learning_rate=0.03, num_leaves=31,
             subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1),
        X_train, y_train, X_val, feature_cols,
    )
    resultados.append(reportar("LightGBM (24 modelos/hora)", pred_lgb, real_val))
    print("\nTop 15 features (importancia media entre las 24 horas) -- LightGBM:")
    print(resumen_importancia(imp_lgb).round(4).to_string())

    # ── Resumen final ────────────────────────────────────────────────────
    resumen = pd.DataFrame(resultados).set_index("modelo")
    print(f"\n{'=' * 60}\nRESUMEN FINAL vs LISTÓN F11\n{'=' * 60}")
    print(resumen.round(2).to_string())

    out = Path(__file__).parent / "resultados_ml_clasico.csv"
    resumen.to_csv(out)
    print(f"\nGuardado: {out}")


if __name__ == "__main__":
    main()
