r"""
TFM Energia UCM - Version minima de Transformer para el precio horario (29-ago-2026)

======================================================================================
OBJETIVO DE APLICACION -- leer esto antes de mirar el numero de MAE
======================================================================================
Este NO es un intento de superar a LightGBM en precision. La evidencia acumulada en el
proyecto (docs/notas_memoria_tfm.md notas 14, 18, 19, 22, 23) apunta en contra de esa
expectativa:
  - El modelo puntual actual (LightGBM + lags de 24h/168h) ya funciona muy bien en
    condiciones normales -- el problema identificado es exposicion a eventos extremos,
    no falta de estructura temporal capturada.
  - El propio Seq2Seq de un compañero de equipo (nota 23) tuvo PEOR MAE que su LightGBM
    (14,39 vs 14,24) sobre el mismo problema.
  - Con ~1.800 dias de entrenamiento, este es un dataset MODESTO para un Transformer --
    la literatura de forecasting (M4/M5, "Are Transformers Effective for Time Series
    Forecasting?") documenta que los arboles de gradiente igualan o superan a los
    Transformers en este regimen de tamaño de datos, sobre todo cuando ya hay features
    exogenas bien construidas (que es exactamente nuestro caso).

El objetivo real de esta version minima es DOBLE:
  1. Comprobacion de robustez: si un Transformer que ve la secuencia CRUDA de precio
     (168 horas reales, sin la ingenieria de lags manual) no logra acercarse a LightGBM,
     es una confirmacion mas de que la ingenieria de features actual ya captura lo que
     importa -- un resultado negativo aqui SI es informativo para la memoria.
  2. Forma del dia completo: al predecir las 24 horas del dia objetivo A LA VEZ (en vez de
     una por una), el Transformer podria producir una forma diaria mas coherente -- lo que
     en la nota 23 resulto ser mas relevante para el valor economico (captura de arbitraje)
     que para el MAE puro, exactamente el patron que encontro el Seq2Seq del compañero.

MEJOR RESULTADO REALISTA ESPERADO (calibrado con la evidencia anterior, no una esperanza
generica): un MAE entre 13,5 y 16 EUR/MWh -- en el rango del Seq2Seq del compañero, por
encima de nuestro LightGBM (12,9), pero potencialmente con mejor captura de arbitraje u
hora de pico. Un MAE por debajo de LightGBM seria una sorpresa real, no lo esperable.

======================================================================================
ARQUITECTURA
======================================================================================
Encoder-decoder pequeño, con atencion, sobre la matriz "nucleo":
  - ENCODER: ve la secuencia de las 168 horas REALES de precio anteriores al dia objetivo
    (una semana completa, ya ocurrida -- sin fuga). Aporta lo que un Transformer promete:
    atencion sobre la historia cruda, no solo 2 puntos de lag (D-1, D-6) como hace la
    version tabular.
  - DECODER: para cada una de las 24 horas del dia objetivo, consume las features "seguras"
    de esa hora (previsiones, calendario, NTC, capacidad -- las mismas 122 columnas que usa
    LightGBM) y hace atencion cruzada sobre la salida del encoder.
  - Salida: precio de esa hora. 24 salidas por dia, todas a la vez (secuencia completa).

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/transformer_minimo_horario.py
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

REPO = Path(__file__).parent.parent
DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
VENTANA_ENCODER = 168  # una semana completa de historia real, sin fuga
HORAS_DIA = 24

COLS_CONTROL = ["fecha_pred", "fecha_objetivo", "ts", "split", "hora"]
COLS_OTROS = ["meteo_es_forecast", "imputado_apagon", "ventana_pisa_apagon",
              "pbf_publicado_D", "pbf_completo_D"]
COLS_OBJETIVO = "target_price"
COLS_ARRANQUE_TARDIO = [
    "capinst_battery_hybrid_mw", "capinst_solar_pv_hybrid_mw", "capinst_wind_hybrid_mw",
    "ree_cbattery_mw_Dm1", "ree_cbattery_mw_Dm6", "ree_gbattery_mw_Dm1", "ree_gbattery_mw_Dm6",
]


def cargar_datos():
    df = pd.read_parquet(REPO / "data" / "gold" / "matriz_nucleo.parquet")
    excluir = set(COLS_CONTROL + COLS_OTROS + COLS_ARRANQUE_TARDIO + [COLS_OBJETIVO])
    feature_cols = [c for c in df.columns if c not in excluir]

    # --- Serie continua de precio REAL, para el encoder (168h de historia sin fuga) ---
    print("Construyendo serie continua de precio real (para el encoder)...")
    import sys
    sys.path.append(str(REPO / "modelos"))
    from construir_dataset_horario import _conectar, _grid_horario, _serie_horaria
    conn = _conectar()
    idx = _grid_horario()
    precio_real = _serie_horaria(conn, "spot_price", ["es_esios"], idx)["es_esios"]
    conn.close()
    precio_real = precio_real.ffill()  # el unico hueco conocido (26-oct DST), ver nota 28

    return df, feature_cols, precio_real


def construir_tensores(df, feature_cols, precio_real, split):
    """Agrupa por dia objetivo: encoder = 168h reales antes del dia, decoder = 24 filas
    de features seguras de ese dia, target = 24 precios reales de ese dia."""
    sub = df[df["split"] == split].sort_values(["fecha_objetivo", "hora"])
    dias = sub["fecha_objetivo"].unique()

    X_enc, X_dec, Y, dias_validos = [], [], [], []
    for dia in dias:
        grupo = sub[sub["fecha_objetivo"] == dia]
        if len(grupo) != HORAS_DIA:
            continue  # dia incompleto -- se descarta, no se rellena con supuestos
        # "ts" en nucleo es hora LOCAL de Madrid, naive (verificado contra spot_price: ts
        # 2020-01-12 04:00 local == 03:00 UTC en pleno invierno, CET=UTC+1) -- precio_real
        # esta en UTC, asi que hay que localizar explicitamente antes de alinear, o el
        # encoder queda desplazado 1-2 horas segun la epoca del año (horario de verano).
        inicio_local = pd.Timestamp(grupo["ts"].min())
        inicio_dia = inicio_local.tz_localize("Europe/Madrid").tz_convert("UTC")
        ventana = precio_real.loc[inicio_dia - pd.Timedelta(hours=VENTANA_ENCODER): inicio_dia - pd.Timedelta(hours=1)]
        if len(ventana) != VENTANA_ENCODER or ventana.isna().any():
            continue  # sin suficiente historia (arranque del dataset) -- se descarta
        X_enc.append(ventana.values)
        X_dec.append(grupo.sort_values("hora")[feature_cols].values)
        Y.append(grupo.sort_values("hora")[COLS_OBJETIVO].values)
        dias_validos.append(dia)

    return (np.array(X_enc, dtype="float32"), np.array(X_dec, dtype="float32"),
            np.array(Y, dtype="float32"), dias_validos)


def bloque_atencion(x, memoria, d_model, n_heads, causal=False):
    """Un bloque encoder (memoria=None) o decoder (memoria=salida del encoder)."""
    attn_out = layers.MultiHeadAttention(num_heads=n_heads, key_dim=d_model // n_heads)(
        x, x, use_causal_mask=causal)
    x = layers.LayerNormalization()(x + attn_out)
    if memoria is not None:
        cross = layers.MultiHeadAttention(num_heads=n_heads, key_dim=d_model // n_heads)(x, memoria)
        x = layers.LayerNormalization()(x + cross)
    ff = layers.Dense(d_model * 2, activation="relu")(x)
    ff = layers.Dense(d_model)(ff)
    return layers.LayerNormalization()(x + ff)


def construir_modelo(n_features, d_model=32, n_heads=4, n_capas=2):
    enc_in = keras.Input(shape=(VENTANA_ENCODER, 1), name="encoder_precio_real")
    dec_in = keras.Input(shape=(HORAS_DIA, n_features), name="decoder_features_seguras")

    pos_enc = layers.Embedding(VENTANA_ENCODER, d_model)(tf.range(VENTANA_ENCODER))
    x = layers.Dense(d_model)(enc_in) + pos_enc
    for _ in range(n_capas):
        x = bloque_atencion(x, memoria=None, d_model=d_model, n_heads=n_heads)
    memoria = x

    pos_dec = layers.Embedding(HORAS_DIA, d_model)(tf.range(HORAS_DIA))
    y = layers.Dense(d_model)(dec_in) + pos_dec
    for _ in range(n_capas):
        y = bloque_atencion(y, memoria=memoria, d_model=d_model, n_heads=n_heads)

    salida = layers.Dense(1)(y)
    salida = layers.Reshape((HORAS_DIA,))(salida)
    return keras.Model([enc_in, dec_in], salida)


def main():
    DIR_ARTEFACTOS.mkdir(exist_ok=True)
    df, feature_cols, precio_real = cargar_datos()
    print(f"{len(feature_cols)} features de entrada para el decoder")

    print("\nConstruyendo tensores de train...")
    Xe_tr, Xd_tr, Y_tr, _ = construir_tensores(df, feature_cols, precio_real, "train")
    print(f"  {len(Y_tr)} dias completos de train")
    print("Construyendo tensores de validation...")
    Xe_val, Xd_val, Y_val, _ = construir_tensores(df, feature_cols, precio_real, "validation")
    print(f"  {len(Y_val)} dias completos de validation")

    # --- Normalizacion: SOLO con estadisticos de train, para no filtrar informacion ---
    mu_enc, sd_enc = Xe_tr.mean(), Xe_tr.std()
    Xe_tr_n = (Xe_tr - mu_enc) / sd_enc
    Xe_val_n = (Xe_val - mu_enc) / sd_enc

    mu_dec = Xd_tr.reshape(-1, Xd_tr.shape[-1]).mean(axis=0)
    sd_dec = Xd_tr.reshape(-1, Xd_tr.shape[-1]).std(axis=0) + 1e-6
    Xd_tr_n = (Xd_tr - mu_dec) / sd_dec
    Xd_val_n = (Xd_val - mu_dec) / sd_dec

    Xe_tr_n = Xe_tr_n[..., np.newaxis]
    Xe_val_n = Xe_val_n[..., np.newaxis]

    print("\nConstruyendo el modelo (Transformer encoder-decoder, d_model=32, 2 capas)...")
    modelo = construir_modelo(n_features=len(feature_cols))
    modelo.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mae")
    modelo.summary()

    print("\nEntrenando (CPU, sin GPU disponible -- modelo pequeño a proposito)...")
    early_stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=8, restore_best_weights=True)
    hist = modelo.fit(
        [Xe_tr_n, Xd_tr_n], Y_tr,
        validation_data=([Xe_val_n, Xd_val_n], Y_val),
        epochs=60, batch_size=32, callbacks=[early_stop], verbose=2,
    )

    pred_val = modelo.predict([Xe_val_n, Xd_val_n], verbose=0)
    mae = np.abs(pred_val - Y_val).mean()
    print(f"\n=== MAE Transformer sobre validation: {mae:.2f} EUR/MWh ===")
    print("Referencia LightGBM sobre la misma matriz nucleo: 12,92 EUR/MWh")

    # --- Metricas economicas, mismo criterio que metricas_economicas_horario.py ---
    n = np.arange(len(Y_val))
    h_min_pred, h_max_pred = pred_val.argmin(1), pred_val.argmax(1)
    h_max_real = Y_val.argmax(1)
    spread_perfecto = np.maximum(Y_val.max(1) - Y_val.min(1), 0.1)
    spread_capturado = Y_val[n, h_max_pred] - Y_val[n, h_min_pred]
    captura = 100 * spread_capturado / spread_perfecto
    pico_1h = 100 * (np.abs(h_max_pred - h_max_real) <= 1).mean()
    print(f"Captura de arbitraje: {captura.mean():.1f}%   Acierto hora pico ±1h: {pico_1h:.1f}%")
    print("Referencia LightGBM (nuestra matriz horaria, validation): captura 91,0%, pico 79,4%")

    modelo.save(DIR_ARTEFACTOS / "transformer_minimo_horario.keras")
    resumen = pd.DataFrame([{
        "modelo": "transformer_minimo", "MAE": round(float(mae), 2),
        "captura_arbitraje_%": round(float(captura.mean()), 1),
        "pico_1h_%": round(float(pico_1h), 1), "n_dias_val": len(Y_val),
        "epochs_entrenados": len(hist.history["loss"]),
    }])
    resumen.to_csv(REPO / "data_temp" / "transformer_minimo_resumen.csv", index=False)
    print(f"\nGuardado: data_temp/transformer_minimo_resumen.csv")


if __name__ == "__main__":
    main()
