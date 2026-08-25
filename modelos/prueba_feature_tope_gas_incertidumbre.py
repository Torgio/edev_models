r"""
TFM Energia UCM - Prueba de una feature nueva (regimen_tope_gas) sobre la capa de incertidumbre (25-ago-2026)

Motivacion: se anadio `regimen_tope_gas` (indicador binario del mecanismo iberico de tope al gas,
vigente 15-jun-2022 a 31-dic-2023, verificado de forma independiente -- ver
`construir_dataset_horario.py`) al dataset horario. Antes de adoptarla en el pipeline de
produccion, se prueba de forma A/B especificamente sobre la capa de incertidumbre (p10/p90),
que es donde mas interesa que ayude: 2022 es justo el periodo peor cubierto en la prueba de
estres de la crisis (nota 22 de docs/notas_memoria_tfm.md).

Compara, con los MISMOS hiperparametros y la MISMA semilla en ambos casos:
  A) modelo de incertidumbre CON regimen_tope_gas como feature
  B) el mismo modelo SIN esa feature (baseline actual)
sobre cobertura y ancho del intervalo [p10, p90], separado por evento extremo vs. normal.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/prueba_feature_tope_gas_incertidumbre.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

warnings.filterwarnings("ignore")

sys.path.append(str(Path(__file__).parent))
from construir_dataset_horario import construir_dataset_horario, dividir_train_val_test_horario
from construir_dataset_maestro import EVENTOS_EXTREMOS

MEJORES_PARAMS = {
    "n_estimators": 1900, "num_leaves": 27, "max_depth": 10,
    "learning_rate": 0.017265119138779546, "subsample": 0.597032055679207,
    "colsample_bytree": 0.7696132252880693, "reg_alpha": 0.0010694942634823176,
    "reg_lambda": 0.0228121782480138, "min_child_samples": 8,
    "subsample_freq": 1, "random_state": 42, "n_jobs": -1, "verbosity": -1,
}


def _marcar_eventos_extremos(idx_horas, dia_d):
    fechas_evento = set()
    for ev in EVENTOS_EXTREMOS:
        if ev["excluir"]:
            fechas_evento.update(d.date() for d in pd.date_range(ev["inicio"], ev["fin"], freq="D"))
    d_mas_1 = pd.Series([(pd.Timestamp(d) + pd.Timedelta(days=1)).date() for d in dia_d], index=idx_horas)
    d_menos_1 = pd.Series([(pd.Timestamp(d) - pd.Timedelta(days=1)).date() for d in dia_d], index=idx_horas)
    d_menos_7 = pd.Series([(pd.Timestamp(d) - pd.Timedelta(days=7)).date() for d in dia_d], index=idx_horas)
    return d_mas_1.isin(fechas_evento) | d_menos_1.isin(fechas_evento) | d_menos_7.isin(fechas_evento)


def _entrenar_p10_p90(X_train, y_train, X_val):
    modelos = {}
    for nombre, alpha in [("p10", 0.10), ("p90", 0.90)]:
        m = LGBMRegressor(objective="quantile", alpha=alpha, **MEJORES_PARAMS)
        m.fit(X_train, y_train)
        modelos[nombre] = m
    return modelos["p10"].predict(X_val), modelos["p90"].predict(X_val)


def _reportar(etiqueta, y_val, p10, p90, evento):
    dentro = (y_val >= p10) & (y_val <= p90)
    ancho = p90 - p10
    print(f"\n=== {etiqueta} ===")
    print(f"  Cobertura global: {dentro.mean()*100:.1f}%   Ancho medio: {ancho.mean():.2f} EUR/MWh")
    print(f"  Cobertura normal: {dentro[~evento].mean()*100:.1f}%   "
          f"Cobertura evento extremo: {dentro[evento].mean()*100:.1f}%  ({evento.sum()} horas)")
    print(f"  Ancho normal: {ancho[~evento].mean():.2f}   Ancho evento extremo: {ancho[evento].mean():.2f}")
    return {"cobertura_global": round(dentro.mean()*100, 1), "ancho_medio": round(ancho.mean(), 2),
            "cobertura_normal": round(dentro[~evento].mean()*100, 1),
            "cobertura_evento": round(dentro[evento].mean()*100, 1),
            "ancho_normal": round(ancho[~evento].mean(), 2), "ancho_evento": round(ancho[evento].mean(), 2)}


def main():
    print("Construyendo dataset horario (pdbc='lag'), ya con regimen_tope_gas incluido...")
    dataset = construir_dataset_horario(pdbc="lag")
    train, val, _test = dividir_train_val_test_horario(dataset)
    assert "regimen_tope_gas" in dataset.columns, "regimen_tope_gas no esta en el dataset"

    feature_cols = [c for c in dataset.columns if c != "precio"]
    X_train_full, y_train = train[feature_cols], train["precio"].values
    X_val_full, y_val = val[feature_cols], val["precio"].values

    medianas = X_train_full.median(numeric_only=True)
    X_train_full = X_train_full.select_dtypes(include=[np.number]).fillna(medianas)
    X_val_full = X_val_full[X_train_full.columns].fillna(medianas)

    dia_d = (val.index.tz_convert("Europe/Madrid") - pd.Timedelta(days=1)).date
    evento = _marcar_eventos_extremos(val.index, dia_d).values

    print(f"\n{evento.sum()} de {len(evento)} horas de validation marcadas como evento extremo.")

    # --- A) CON regimen_tope_gas ---
    p10_a, p90_a = _entrenar_p10_p90(X_train_full, y_train, X_val_full)
    res_a = _reportar("A) CON regimen_tope_gas", y_val, p10_a, p90_a, evento)

    # --- B) SIN regimen_tope_gas (baseline) ---
    X_train_b = X_train_full.drop(columns=["regimen_tope_gas"])
    X_val_b = X_val_full.drop(columns=["regimen_tope_gas"])
    p10_b, p90_b = _entrenar_p10_p90(X_train_b, y_train, X_val_b)
    res_b = _reportar("B) SIN regimen_tope_gas (baseline)", y_val, p10_b, p90_b, evento)

    print("\n=== Diferencia (A - B) ===")
    for k in res_a:
        print(f"  {k}: {res_a[k] - res_b[k]:+.2f}")

    df_resumen = pd.DataFrame([{"variante": "con_regimen_tope_gas", **res_a},
                                {"variante": "sin_regimen_tope_gas", **res_b}])
    ruta = Path(__file__).parent.parent / "data_temp" / "prueba_feature_tope_gas_incertidumbre.csv"
    df_resumen.to_csv(ruta, index=False)
    print(f"\nGuardado: {ruta}")


if __name__ == "__main__":
    main()
