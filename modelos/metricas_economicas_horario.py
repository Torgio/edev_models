r"""
TFM Energia UCM - Metricas de valor economico sobre el modelo horario ganador (25-ago-2026)

Motivacion (ver docs/notas_memoria_tfm.md nota 23): el companero de equipo encontro que el MAE no
siempre coincide con el valor economico de una prediccion -- su Seq2Seq tenia peor MAE que su
LightGBM pero mejor "captura de arbitraje" y mejor acierto de hora pico, justo las dos metricas
que le importan al capitulo de baterias (cap. 5/7). Se implementan aqui esas dos metricas sobre
NUESTRO modelo LightGBM horario ya entrenado (`lightgbm_horario_final.joblib`, MAE 12,55), sin
reentrenar nada.

Metricas (formula identica a la del companero, ver `entrenar_modelos_sergio.py`):

  - captura de arbitraje: de cada dia D+1 (24 horas), se identifica la hora de precio minimo
    PREDICHA (donde "cargaria" una bateria) y la hora de precio maximo PREDICHA (donde
    "descargaria"). La ganancia real obtenida (precio real en la hora de descarga - precio real en
    la hora de carga) se divide entre el arbitraje PERFECTO de ese dia (precio real maximo - precio
    real minimo, con un piso de 0,1 para evitar dividir por casi cero en dias muy planos). 100% =
    el modelo hubiera permitido capturar todo el valor economico posible ese dia, aunque no
    acertara el precio exacto.
  - acierto de hora de pico ±1h: si la hora de precio maximo predicha cae a 1 hora o menos de la
    hora de precio maximo real.

Estructura de datos: nuestro dataset es 1 fila = 1 hora (no 1 fila = 1 dia con 24 columnas como el
del companero), asi que aqui se reconstruye el bloque de 24 horas por dia objetivo AGRUPANDO las
predicciones de validation por su propia fecha local (Madrid) -- cada fila del dataset horario ya
es una hora de un dia D+1 concreto.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/metricas_economicas_horario.py
"""

import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.append(str(Path(__file__).parent))
from construir_dataset_horario import construir_dataset_horario, dividir_train_val_test_horario

DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
DIR_DATA_TEMP = Path(__file__).parent.parent / "data_temp"


def _metricas_por_dia(df_dia: pd.DataFrame) -> dict:
    """df_dia: filas de un mismo dia objetivo D+1, con columnas 'real' y 'pred', ordenadas por hora."""
    real = df_dia["real"].values
    pred = df_dia["pred"].values
    if len(real) < 24:
        return None  # dia incompleto (huecos de datos) -- se descarta, no se rellena con supuestos
    h_min_pred, h_max_pred = pred.argmin(), pred.argmax()
    h_max_real = real.argmax()
    spread_perfecto = max(real.max() - real.min(), 0.1)
    spread_capturado = real[h_max_pred] - real[h_min_pred]
    return {
        "captura_pct": 100 * spread_capturado / spread_perfecto,
        "pico_1h": abs(h_max_pred - h_max_real) <= 1,
        "mae_dia": np.abs(real - pred).mean(),
    }


def _evaluar_modelo(nombre: str, df: pd.DataFrame) -> pd.DataFrame:
    """df: index = timestamp UTC de la hora objetivo, columnas 'real' y 'pred'."""
    fecha_local = df.index.tz_convert("Europe/Madrid").date
    filas = []
    for fecha, grupo in df.groupby(fecha_local):
        m = _metricas_por_dia(grupo.sort_index())
        if m is not None:
            m["fecha"] = fecha
            filas.append(m)
    out = pd.DataFrame(filas).set_index("fecha")
    print(f"\n=== {nombre} ({len(out)} dias completos de {len(df)//24} esperados) ===")
    print(f"  MAE medio diario:        {out['mae_dia'].mean():.2f} EUR/MWh")
    print(f"  Captura de arbitraje:    {out['captura_pct'].mean():.1f}%")
    print(f"  Acierto hora pico ±1h:   {100*out['pico_1h'].mean():.1f}%")
    return out


def main():
    print("Cargando modelo LightGBM horario ya entrenado...")
    art = joblib.load(DIR_ARTEFACTOS / "lightgbm_horario_final.joblib")
    modelo = art["modelo"]
    feature_cols = art["feature_cols"]
    medianas = art["medianas"]

    print("Construyendo dataset horario (pdbc='lag')...")
    dataset = construir_dataset_horario(pdbc="lag")
    _train, val, _test = dividir_train_val_test_horario(dataset)

    X_val = val[feature_cols].fillna(medianas)
    pred = modelo.predict(X_val)

    df_modelo = pd.DataFrame({"real": val["precio"].values, "pred": pred}, index=val.index)
    resultado_modelo = _evaluar_modelo("LightGBM horario (MAE 12,55 en punto)", df_modelo)

    # --- Referencia: persistencia (naive D-24h, "el precio de la misma hora ayer") ---
    pred_naive = val["precio_propio_lag24h"].fillna(medianas.get("precio_propio_lag24h", val["precio"].median()))
    df_naive = pd.DataFrame({"real": val["precio"].values, "pred": pred_naive.values}, index=val.index)
    resultado_naive = _evaluar_modelo("Persistencia (naive D-24h)", df_naive)

    resumen = pd.DataFrame({
        "LightGBM horario": [resultado_modelo["mae_dia"].mean(), resultado_modelo["captura_pct"].mean(),
                              100 * resultado_modelo["pico_1h"].mean()],
        "Persistencia (naive)": [resultado_naive["mae_dia"].mean(), resultado_naive["captura_pct"].mean(),
                                  100 * resultado_naive["pico_1h"].mean()],
    }, index=["MAE medio diario", "captura_arbitraje_%", "pico_1h_%"]).round(1)
    print("\n=== Resumen comparativo ===")
    print(resumen.to_string())

    resultado_modelo.to_csv(DIR_DATA_TEMP / "metricas_economicas_lightgbm_horario.csv")
    resumen.to_csv(DIR_DATA_TEMP / "metricas_economicas_resumen.csv")
    print(f"\nGuardado: data_temp/metricas_economicas_lightgbm_horario.csv")
    print(f"Guardado: data_temp/metricas_economicas_resumen.csv")


if __name__ == "__main__":
    main()
