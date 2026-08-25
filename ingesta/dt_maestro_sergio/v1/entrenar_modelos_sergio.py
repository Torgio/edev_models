"""
TFM Energia UCM -- Escalera de modelos para la prediccion del precio diario (D+1)

Entrena y compara, en este orden y sobre EXACTAMENTE el mismo split:

    0. BASELINES        naive D (precio de hoy), naive D-6 (mismo dia de la semana),
                        media de los dos, y LightGBM sobre features aplanadas si esta instalado.
    1. MLP              features aplanadas -> Dense -> 24 salidas.
    2. SEQ2SEQ          encoder LSTM sobre el historico + decoder LSTM alimentado hora a hora
                        con las previsiones de D+1.

POR QUE LOS BASELINES VAN PRIMERO Y NO SON OPCIONALES
El precio electrico tiene una autocorrelacion brutal: repetir el precio de ayer ya acierta
bastante. Un MAE de 8 EUR/MWh no significa nada por si solo -- significa algo comparado con
los 12 que saca el naive. Sin esa referencia el capitulo de deep learning no tiene contra que
medirse, y es lo primero que pregunta un tribunal. Ademas funcionan de detector de fugas: si
la red baja MUCHO del baseline (digamos MAE < 3), lo probable no es que sea buenisima sino que
se ha colado informacion del futuro.

DECISIONES DE MODELADO Y SU MOTIVO
  - Perdida HUBER, no MSE. Los picos de 2022 (>500 EUR/MWh) dominarian el gradiente con MSE y
    el modelo se dedicaria a esos dias a costa de los 2.000 normales.
  - Escalado ajustado SOLO CON TRAIN. Usar la media/desviacion del conjunto completo mete
    informacion de test en el preprocesado; es la fuga mas comun y la mas silenciosa.
  - NADA de Bidirectional en el decoder. En texto es gratis; aqui miraria horas futuras para
    predecir la actual. En el encoder si vale (ese pasado ya ocurrio entero).
  - El decoder recibe X_fut CONCATENADO, no un RepeatVector del contexto. Repetir el estado 24
    veces le da al decoder lo mismo en cada hora; concatenar la prevision horaria le dice que a
    las 14:00 hay 8 GW de solar. Es la diferencia entre el seq2seq de traduccion y este
    problema, y es lo que justifica la arquitectura frente a un arbol.
  - Semilla fijada. Con 4 semanas por delante no hay tiempo para perseguir resultados que no
    se reproducen.

Uso:
    python entrenar_modelos.py                      # busca el .npz mas reciente
    python entrenar_modelos.py --npz tensores_sergio_v01.npz
    python entrenar_modelos.py --solo-baselines     # sin tocar Keras, 5 segundos
    python entrenar_modelos.py --epochs 60 --batch 64

Requisitos: numpy, pandas. Para 1 y 2: tensorflow. Opcional: lightgbm.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEMILLA = 42
np.random.seed(SEMILLA)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  Carga y preparacion
# ══════════════════════════════════════════════════════════════════════════════════════════

def cargar(npz_path: Path):
    d = np.load(npz_path, allow_pickle=True)
    X_hist, X_fut, X_est = d["X_hist"], d["X_fut"], d["X_est"]
    y, fechas = d["y"], d["fechas"]
    tr, va, te = d["train"], d["val"], d["test"]

    print(f"Tensores desde {npz_path.name}")
    print(f"  X_hist {X_hist.shape} | X_fut {X_fut.shape} | X_est {X_est.shape} | y {y.shape}")
    print(f"  train {tr.sum()} dias | val {va.sum()} | test {te.sum()}")
    print(f"  rango: {fechas.min()} -> {fechas.max()}")

    for n, a in [("X_hist", X_hist), ("X_fut", X_fut), ("X_est", X_est), ("y", y)]:
        if np.isnan(a).any():
            raise ValueError(
                f"{n} tiene {np.isnan(a).sum()} NaN. Keras devolveria loss=nan desde la primera "
                f"epoca sin lanzar ningun error. Regenera los tensores con rellenar_ceros=True."
            )
    return X_hist, X_fut, X_est, y, fechas, tr, va, te


class EscaladorCanal:
    """Estandariza por canal. Se AJUSTA SOLO CON TRAIN -- ver nota de cabecera."""

    def __init__(self, eps=1e-6):
        self.eps = eps

    def fit(self, x):
        ejes = tuple(range(x.ndim - 1))          # todo menos el eje de canal
        self.mu = x.mean(axis=ejes, keepdims=True)
        self.sd = x.std(axis=ejes, keepdims=True) + self.eps
        return self

    def transform(self, x):
        return ((x - self.mu) / self.sd).astype("float32")

    def fit_transform(self, x):
        return self.fit(x).transform(x)


class EscaladorTarget:
    """Un unico mu/sd para las 24 salidas: comparten unidad (EUR/MWh) y deben compartir escala.
    Estandarizar cada hora por separado deformaria la curva diaria, que es lo que se predice."""

    def fit(self, y):
        self.mu, self.sd = float(y.mean()), float(y.std())
        return self

    def transform(self, y):
        return ((y - self.mu) / self.sd).astype("float32")

    def inverse(self, y):
        return y * self.sd + self.mu


# ══════════════════════════════════════════════════════════════════════════════════════════
#  Metricas
# ══════════════════════════════════════════════════════════════════════════════════════════

def metricas(y_true, y_pred):
    err = y_pred - y_true
    mae = np.abs(err).mean()
    rmse = np.sqrt((err ** 2).mean())
    # sMAPE en vez de MAPE: el precio pasa por cero y por valores negativos en horas de
    # excedente solar, y ahi el MAPE explota a infinito.
    den = (np.abs(y_true) + np.abs(y_pred)) / 2
    smape = 100 * np.mean(np.abs(err) / np.maximum(den, 1e-3))
    # MAE del dia peor: interesa para bateria, donde un dia malo se come el margen de la semana
    mae_dia = np.abs(err).mean(axis=1)
    return {"MAE": mae, "RMSE": rmse, "sMAPE": smape,
            "MAE_p95_dia": float(np.percentile(mae_dia, 95))}


def tabla(resultados):
    df = pd.DataFrame(resultados).T
    df = df.sort_values("MAE")
    base = df.loc[[i for i in df.index if i.startswith("naive")], "MAE"].min()
    df["vs_naive_%"] = 100 * (df["MAE"] / base - 1)
    return df


# ══════════════════════════════════════════════════════════════════════════════════════════
#  0. Baselines
# ══════════════════════════════════════════════════════════════════════════════════════════

def baselines(X_fut, y, te, cols_fut):
    """El precio del dia D viaja dentro de X_fut como canal `precio_D_misma_hora`."""
    try:
        i_pd = list(cols_fut).index("precio_D_misma_hora")
    except ValueError:
        print("AVISO: no encuentro `precio_D_misma_hora` en X_fut; sin baseline naive")
        return {}
    naive_d = X_fut[:, :, i_pd]
    res = {"naive D (precio de hoy)": metricas(y[te], naive_d[te])}
    return res


def baseline_naive_semana(y, fechas, te):
    """Precio del mismo dia de la semana anterior. Se reconstruye desde `y` buscando el dia -7."""
    idx = {f: i for i, f in enumerate(fechas)}
    pred, real = [], []
    for i, f in enumerate(fechas):
        if not te[i]:
            continue
        j = idx.get(f - np.timedelta64(7, "D"))
        if j is not None:
            pred.append(y[j])
            real.append(y[i])
    if not pred:
        return {}
    return {"naive D-6 (misma semana)": metricas(np.array(real), np.array(pred))}


def baseline_lightgbm(Xtr, ytr, Xva, yva, Xte, yte):
    try:
        import lightgbm as lgb
    except ImportError:
        print("  (lightgbm no instalado; se salta -- `pip install lightgbm`)")
        return {}, None
    print("  LightGBM: 24 modelos, uno por hora...")
    pred = np.zeros_like(yte)
    for h in range(24):
        m = lgb.LGBMRegressor(n_estimators=600, learning_rate=0.05, num_leaves=31,
                              objective="huber", verbose=-1, random_state=SEMILLA)
        m.fit(Xtr, ytr[:, h], eval_set=[(Xva, yva[:, h])],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        pred[:, h] = m.predict(Xte)
    return {"LightGBM (tabular)": metricas(yte, pred)}, pred


# ══════════════════════════════════════════════════════════════════════════════════════════
#  1 y 2. Redes
# ══════════════════════════════════════════════════════════════════════════════════════════

def construir_mlp(n_plano, dropout=0.2):
    from tensorflow import keras
    from tensorflow.keras import layers
    e = keras.Input(shape=(n_plano,), name="plano")
    x = layers.Dense(512, activation="relu")(e)
    x = layers.Dropout(dropout)(x)
    x = layers.Dense(256, activation="relu")(x)
    x = layers.Dropout(dropout)(x)
    s = layers.Dense(24, name="precio")(x)      # lineal: es regresion, no clasificacion
    return keras.Model(e, s, name="MLP")


def construir_seq2seq(sh_hist, sh_fut, n_est, unidades=128, dropout=0.2):
    """Encoder LSTM sobre el historico + decoder LSTM alimentado hora a hora con X_fut.

    El punto clave esta en el Concatenate: el estado del encoder se repite 24 veces Y se une a
    la matriz (24, f) de previsiones. Un RepeatVector solo daria al decoder el mismo vector en
    las 24 horas, sin manera de saber que a las 14:00 entran 8 GW de solar. Ninguno de los
    seq2seq de traduccion hace esto porque alli no existe un exogeno futuro conocido: es la
    particularidad de este problema.
    """
    from tensorflow import keras
    from tensorflow.keras import layers

    in_hist = keras.Input(shape=sh_hist, name="hist")       # (168, m)
    in_fut = keras.Input(shape=sh_fut, name="fut")          # (24, f)
    in_est = keras.Input(shape=(n_est,), name="est")        # (j,)

    # ENCODER -- bidireccional SI: ese pasado ya ocurrio entero, no hay fuga
    enc = layers.Bidirectional(layers.LSTM(unidades, dropout=dropout), name="encoder")(in_hist)
    ctx = layers.Concatenate(name="contexto")([enc, layers.Dense(64, activation="relu")(in_est)])

    ctx_rep = layers.RepeatVector(sh_fut[0])(ctx)
    dec_in = layers.Concatenate(name="contexto_mas_prevision")([ctx_rep, in_fut])

    # DECODER -- unidireccional OBLIGATORIO: bidireccional miraria horas futuras
    dec = layers.LSTM(unidades, return_sequences=True, dropout=dropout, name="decoder")(dec_in)
    dec = layers.Dropout(dropout)(dec)
    out = layers.TimeDistributed(layers.Dense(1), name="precio")(dec)
    out = layers.Reshape((sh_fut[0],))(out)

    return keras.Model([in_hist, in_fut, in_est], out, name="Seq2Seq")


def entrenar(modelo, entradas_tr, ytr, entradas_va, yva, epochs, batch, lr=1e-3):
    from tensorflow import keras
    modelo.compile(optimizer=keras.optimizers.Adam(lr),
                   loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
    cb = [
        keras.callbacks.EarlyStopping(monitor="val_loss", patience=12,
                                      restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=5,
                                          min_lr=1e-5, verbose=0),
    ]
    h = modelo.fit(entradas_tr, ytr, validation_data=(entradas_va, yva),
                   epochs=epochs, batch_size=batch, callbacks=cb, verbose=2, shuffle=True)
    return h


# ══════════════════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Escalera de modelos de precio D+1")
    ap.add_argument("--npz", default=None, help="Ruta al .npz (por defecto, el mas reciente)")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--unidades", type=int, default=128)
    ap.add_argument("--solo-baselines", action="store_true")
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    carpeta = Path(args.salida) if args.salida else Path(__file__).parent
    if args.npz:
        npz = Path(args.npz)
    else:
        cands = sorted(carpeta.glob("tensores*_v[0-9][0-9].npz"))
        if not cands:
            raise SystemExit("No encuentro ningun tensores_*.npz. Lanza antes:\n"
                             "  python construir_dataset_maestro.py --tensores")
        npz = cands[-1]

    X_hist, X_fut, X_est, y, fechas, tr, va, te = cargar(npz)
    cols_fut = list(np.load(npz, allow_pickle=True).get("cols_cols_fut", np.array([])))

    resultados = {}

    # ── 0. Baselines ──────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78 + "\n0. BASELINES\n" + "=" * 78)
    resultados.update(baselines(X_fut, y, te, cols_fut))
    resultados.update(baseline_naive_semana(y, fechas, te))
    for k, v in resultados.items():
        print(f"  {k:<28} MAE {v['MAE']:6.2f}  RMSE {v['RMSE']:6.2f}  sMAPE {v['sMAPE']:5.1f}%")

    # ── escalado (SOLO CON TRAIN) ─────────────────────────────────────────────────────────
    s_h, s_f, s_e, s_y = EscaladorCanal(), EscaladorCanal(), EscaladorCanal(), EscaladorTarget()
    s_h.fit(X_hist[tr]); s_f.fit(X_fut[tr]); s_e.fit(X_est[tr]); s_y.fit(y[tr])
    Xh, Xf, Xe = s_h.transform(X_hist), s_f.transform(X_fut), s_e.transform(X_est)
    ys = s_y.transform(y)

    plano = np.concatenate([Xh.reshape(len(Xh), -1), Xf.reshape(len(Xf), -1), Xe], axis=1)
    print(f"\nMatriz plana para modelos tabulares: {plano.shape}")

    lgb_res, _ = baseline_lightgbm(plano[tr], y[tr], plano[va], y[va], plano[te], y[te])
    resultados.update(lgb_res)

    if args.solo_baselines:
        print("\n" + tabla(resultados).round(2).to_string())
        return

    try:
        import tensorflow as tf
    except ImportError:
        raise SystemExit("\nFalta tensorflow (`pip install tensorflow`). Con --solo-baselines "
                         "puedes lanzar la parte que no lo necesita.")
    tf.random.set_seed(SEMILLA)

    predicciones = {}

    # ── 1. MLP ────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78 + "\n1. MLP multi-salida\n" + "=" * 78)
    mlp = construir_mlp(plano.shape[1])
    mlp.summary()
    entrenar(mlp, plano[tr], ys[tr], plano[va], ys[va], args.epochs, args.batch)
    p = s_y.inverse(mlp.predict(plano[te], verbose=0))
    resultados["MLP"] = metricas(y[te], p)
    predicciones["MLP"] = p

    # ── 2. Seq2Seq ────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78 + "\n2. Seq2Seq encoder-decoder\n" + "=" * 78)
    s2s = construir_seq2seq(Xh.shape[1:], Xf.shape[1:], Xe.shape[1], unidades=args.unidades)
    s2s.summary()
    ent_tr = {"hist": Xh[tr], "fut": Xf[tr], "est": Xe[tr]}
    ent_va = {"hist": Xh[va], "fut": Xf[va], "est": Xe[va]}
    ent_te = {"hist": Xh[te], "fut": Xf[te], "est": Xe[te]}
    entrenar(s2s, ent_tr, ys[tr], ent_va, ys[va], args.epochs, args.batch)
    p = s_y.inverse(s2s.predict(ent_te, verbose=0))
    resultados["Seq2Seq"] = metricas(y[te], p)
    predicciones["Seq2Seq"] = p

    # ── comparativa ───────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78 + "\nRESULTADOS EN TEST (EUR/MWh)\n" + "=" * 78)
    t = tabla(resultados)
    print(t.round(2).to_string())

    mejor = t.index[0]
    print(f"\nMejor: {mejor}")
    if t.loc[mejor, "MAE"] < 3:
        print("  *** MAE sospechosamente bajo. Antes de celebrarlo, revisa fugas: algun canal")
        print("      de X_fut que describa D+1 con informacion posterior al cierre. ***")

    if mejor in predicciones:
        mae_h = np.abs(predicciones[mejor] - y[te]).mean(axis=0)
        print("\nMAE por hora del mejor modelo:")
        for bloque in range(0, 24, 12):
            print("  h " + " ".join(f"{h:5d}" for h in range(bloque, bloque + 12)))
            print("    " + " ".join(f"{mae_h[h]:5.1f}" for h in range(bloque, bloque + 12)))
        print("  (las horas peores suelen ser las de rampa solar, 8-10 y 18-20)")

    out = carpeta / "resultados_modelos.csv"
    t.to_csv(out)
    if predicciones:
        np.savez_compressed(carpeta / "predicciones_test.npz",
                            y_true=y[te], fechas=fechas[te], **predicciones)
    print(f"\nGuardado: {out.name} y predicciones_test.npz")


if __name__ == "__main__":
    main()
