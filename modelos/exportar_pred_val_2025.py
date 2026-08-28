r"""
TFM Energia UCM - Exportar predicciones de validation 2025 para el leaderboard del equipo (26-ago-2026)

Motivacion: la propuesta de productivizacion del equipo ("Doce modelos, una tabla") pide que cada
autor entregue, antes del domingo 30-ago, un CSV de predicciones sobre 2025 + un metadata.json --
NO el MAE propio, NO la captura de arbitraje propia: eso lo calcula un evaluador comun para los
doce, con el mismo codigo, para que los numeros sean comparables (evita la inconsistencia ya
detectada: la captura de "persistencia_d1" salio 86,5% en F11_baselines.ipynb y 81% en nuestra
propia radiografia, porque cada notebook uso su propia formula de arbitraje -- ver
docs/notas_memoria_tfm.md nota 27).

Genera:
  modelos/lgbm_horario_afinado_cqr/pred_val_2025.csv   -- formato fijo pedido por el equipo
  modelos/lgbm_horario_afinado_cqr/metadata.json

precio_pred = modelo puntual (Optuna, MAE 12,55 sobre validation).
p10/p90 = intervalo YA CALIBRADO (calibracion conforme, Q=+2,90 EUR/MWh) -- no el crudo, porque es
el que de verdad cumple la cobertura del 80% (ver nota 21).

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/exportar_pred_val_2025.py
"""

import json
import sys
import warnings
from pathlib import Path

import joblib
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.append(str(Path(__file__).parent))
from construir_dataset_horario import construir_dataset_horario

DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
MODELO_ID = "lgbm_horario_afinado_cqr"
OUT_DIR = Path(__file__).parent / MODELO_ID


def main():
    OUT_DIR.mkdir(exist_ok=True)

    print("Cargando modelo puntual y capa de incertidumbre ya entrenados...")
    art_puntual = joblib.load(DIR_ARTEFACTOS / "lightgbm_horario_final.joblib")
    art_calib = joblib.load(DIR_ARTEFACTOS / "calibracion_conforme_horario.joblib")
    modelo = art_puntual["modelo"]
    feature_cols = art_puntual["feature_cols"]
    medianas = art_puntual["medianas"]
    modelo_p10 = art_calib["modelo_p10"]
    modelo_p90 = art_calib["modelo_p90"]
    Q = art_calib["Q"]
    print(f"  Q de calibracion conforme: {Q:+.2f} EUR/MWh")

    print("Construyendo dataset horario (pdbc='lag')...")
    dataset = construir_dataset_horario(pdbc="lag")

    # Filtra por la hora OBJETIVO (datetime_utc), no por el dia D que hace la prediccion -- el
    # equipo pidio "las horas de 2025" refiriendose a la hora que se predice.
    ventana_2025 = dataset[(dataset.index >= "2025-01-01") & (dataset.index < "2026-01-01")]
    print(f"Horas de 2025 encontradas en el dataset: {len(ventana_2025)} (se esperan 8760)")

    X = ventana_2025[feature_cols].fillna(medianas)
    precio_pred = modelo.predict(X)
    p10_crudo = modelo_p10.predict(X)
    p90_crudo = modelo_p90.predict(X)

    df_out = pd.DataFrame({
        "modelo_id": MODELO_ID,
        "datetime_utc": ventana_2025.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "precio_pred": precio_pred.round(2),
        "p10": (p10_crudo - Q).round(2),
        "p90": (p90_crudo + Q).round(2),
    })

    ruta_csv = OUT_DIR / "pred_val_2025.csv"
    df_out.to_csv(ruta_csv, index=False)
    print(f"Guardado: {ruta_csv} ({len(df_out)} filas)")

    metadata = {
        "modelo_id": MODELO_ID,
        "version": "v1",
        "familia": "boosting",
        "autor": "Willy",
        "libreria": "lightgbm==4.7.0",
        "python": "3.13.14",
        "entrenado_desde": "2020-01-01",
        "entrenado_hasta": "2024-12-31",
        "semilla": 42,
        "features": feature_cols,
        "features_dudosas": [],
        "artefacto": "modelos/artefactos/lightgbm_horario_final.joblib",
        "notas": (
            "Afinado con Optuna, 300 pruebas (MAE 12,55 EUR/MWh sobre validation 2025). "
            "p10/p90 con regresion cuantilica (mismos hiperparametros) + calibracion conforme "
            "(CQR, Q=+2.90 EUR/MWh) -- cobertura verificada 79,3% global / 71,8% en evento "
            "extremo (objetivo 80%). Dataset horario: una fila por hora objetivo D+1, lags "
            "24h/168h para datos reales, join por timestamp exacto para previsiones seguras. "
            "Ver docs/notas_memoria_tfm.md notas 15-26 para el detalle completo de cada decision."
        ),
    }
    ruta_meta = OUT_DIR / "metadata.json"
    with open(ruta_meta, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"Guardado: {ruta_meta}")


if __name__ == "__main__":
    main()
