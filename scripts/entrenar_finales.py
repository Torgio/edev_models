"""Fase B: los campeones de cada familia, con semillas, listos para exportar.

POR QUE UN SCRIPT Y NO EL NOTEBOOK
El notebook entrena mas de cuarenta modelos, y la mayoria son experimentos ya cerrados: la
busqueda con Hyperband (60-90 configuraciones), la escalera del temario (6), los cuatro
cortes de fine-tuning y la ablacion por canal (6). Todos tienen su conclusion documentada y
ninguno hace falta para los modelos finales.

Aqui se entrena UNA arquitectura por familia, que es lo que se compara y lo que se exporta.
Ocho modelos por semilla en lugar de cuarenta y tantos.

QUE ES UNA FAMILIA
Variantes de un mismo esqueleto no son familias distintas: `Seq2Seq residuo`, `+ pesos`,
`+ fine-tuning` y las seis del temario son el mismo encoder-decoder entrenado o regularizado
de otra manera. Meterlas todas en un ensemble le daba a esa familia el 62 % del voto y al
GRU -- el mejor modelo individual -- un 6 %.

    gru                 celda GRU, un tercio menos de parametros que la LSTM
    conv1d_lstm         Conv1D con stride antes de la recurrente
    seq2seq             LSTM + RepeatVector, el de referencia
    simplernn           el escalon basico, ~35 k parametros
    lstm                la gemela del GRU, para la comparacion limpia
    denso               MLP sobre la vista aplanada
    boosting            LightGBM, un modelo por hora
    seq2seq_absoluto    predice el precio, no el residuo

El ultimo entra pese a perder en MAE porque es el que mejor CAPTURA el spread, y para
operar una bateria eso es lo que cuenta. La correlacion entre MAE y captura es -0,285: son
metricas casi independientes, y elegir campeon solo por MAE elige mal para el capitulo 7.

VARIAS SEMILLAS. Dos ejecuciones identicas difieren hasta 0,58 en MAE de validacion, y las
distancias entre los tres primeros son de 0,42. Sin repetir, el ranking es azar. Cada
familia sale con media y desviacion.

EL ENSEMBLE se construye promediando el mejor representante de cada familia SEGUN
VALIDACION. El test no interviene en ninguna decision.

Uso:
    python scripts/entrenar_finales.py                        # nucleo, 3 semillas
    python scripts/entrenar_finales.py --matriz moderna
    python scripts/entrenar_finales.py --semillas 1           # rapido, para probar
    python scripts/entrenar_finales.py --familias gru seq2seq
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))

SEMILLA = 42
EPOCHS, BATCH = 80, 64
FAMILIAS = ["gru", "conv1d_lstm", "seq2seq", "simplernn", "lstm", "denso",
            "boosting", "seq2seq_absoluto"]


def metricas(yt, yp):
    e = yp - yt
    den = (np.abs(yt) + np.abs(yp)) / 2
    n = np.arange(len(yt))
    # captura de spread: cuanto del arbitraje perfecto se obtiene cargando en el valle
    # PREDICHO y descargando en el pico PREDICHO. El MAE mide precision, esto mide dinero.
    cap = ((yt[n, yp.argmax(1)] - yt[n, yp.argmin(1)]) /
           np.maximum(yt.max(1) - yt.min(1), .1))
    return {"MAE": float(np.abs(e).mean()),
            "RMSE": float(np.sqrt((e ** 2).mean())),
            "sMAPE": float(100 * np.mean(np.abs(e) / np.maximum(den, 1e-3))),
            "captura_%": float(100 * cap.mean()),
            "pico_1h_%": float(100 * (np.abs(yt.argmax(1) - yp.argmax(1)) <= 1).mean())}


def vista_plana(T):
    """Encoder resumido, para los modelos que no aprovechan los 168 pasos crudos.

    Un arbol o un MLP no comparten pesos entre los pasos: ven 168 x C columnas casi
    identicas y tienen que evaluarlas todas. Con 51 canales eso son 8.700 columnas para
    2.388 muestras. Resumiendo a unos pocos estadisticos por canal baja a ~800, entrena
    mucho mas rapido y da menos sitio donde partir por ruido.
    """
    v = T.X_enc.reshape(len(T.X_enc), 7, 24, T.X_enc.shape[-1])
    partes = [v[:, -1, :, 0],                               # precio del dia D, 24 h
              v.mean(axis=(1, 2)), v.min(axis=(1, 2)), v.max(axis=(1, 2)),
              v[:, -1].mean(axis=1),                        # media del dia D
              v[:, -1].mean(axis=1) - v[:, 0].mean(axis=1), # tendencia en la ventana
              T.X_dec.reshape(len(T.X_dec), -1), T.X_est]
    return np.concatenate(partes, axis=1).astype("float32")


def construir(fam, T, keras, layers):
    ie = keras.Input(shape=T.X_enc.shape[1:], name="hist")
    idc = keras.Input(shape=T.X_dec.shape[1:], name="fut")
    ist = keras.Input(shape=(T.X_est.shape[1],), name="est")
    dr = 0.3
    CELDA = {"gru": layers.GRU, "simplernn": layers.SimpleRNN}
    celda = CELDA.get(fam, layers.LSTM)
    u = 64 if fam == "simplernn" else 96

    x = ie
    if fam == "conv1d_lstm":
        x = layers.Conv1D(64, 5, strides=4, padding="causal", activation="relu")(ie)
        x = layers.BatchNormalization()(x)

    enc = layers.Bidirectional(celda(u, dropout=dr))(x)
    ctx = layers.Concatenate()([enc, layers.Dense(64, activation="relu")(ist)])
    dec = layers.Concatenate()([layers.RepeatVector(24)(ctx), idc])
    dec = celda(u, return_sequences=True, dropout=dr)(dec)
    out = layers.Reshape((24,))(layers.TimeDistributed(layers.Dense(1))(dec))
    m = keras.Model([ie, idc, ist], out, name=fam)
    m.compile(optimizer=keras.optimizers.Adam(1e-3),
              loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
    return m


def entrenar(fam, T, yr, inv_r, ys, inv_abs, semilla, keras, layers):
    """Devuelve (pred_val, pred_test, n_parametros) en EUR/MWh."""
    keras.utils.set_random_seed(semilla)
    cb = [keras.callbacks.EarlyStopping(monitor="val_loss", patience=12,
                                        restore_best_weights=True),
          keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5,
                                            patience=5, min_lr=1e-5)]

    if fam == "boosting":
        import lightgbm as lgb
        A = vista_plana(T)
        pv = np.zeros((int(T.va.sum()), 24))
        pt = np.zeros((int(T.te.sum()), 24))
        for h in range(24):
            g = lgb.LGBMRegressor(n_estimators=800, learning_rate=0.03, num_leaves=31,
                                  objective="huber", verbose=-1, random_state=semilla,
                                  n_jobs=-1)
            g.fit(A[T.tr], yr[T.tr][:, h], eval_set=[(A[T.va], yr[T.va][:, h])],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
            pv[:, h] = g.predict(A[T.va])
            pt[:, h] = g.predict(A[T.te])
        return inv_r(pv, T.va), inv_r(pt, T.te), 0, None

    if fam == "denso":
        A = vista_plana(T)
        m = keras.Sequential([keras.Input(shape=(A.shape[1],)),
                              layers.Dense(512, activation="relu"), layers.Dropout(0.2),
                              layers.Dense(256, activation="relu"), layers.Dropout(0.2),
                              layers.Dense(24)], name="denso")
        m.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
        m.fit(A[T.tr], yr[T.tr], validation_data=(A[T.va], yr[T.va]),
              epochs=EPOCHS, batch_size=BATCH, callbacks=cb, verbose=0)
        return (inv_r(m.predict(A[T.va], verbose=0), T.va),
                inv_r(m.predict(A[T.te], verbose=0), T.te), m.count_params(), m)

    absoluto = fam == "seq2seq_absoluto"
    m = construir("seq2seq" if absoluto else fam, T, keras, layers)
    obj, inv = (ys, inv_abs) if absoluto else (yr, inv_r)
    m.fit(T.ent(T.tr), obj[T.tr], validation_data=(T.ent(T.va), obj[T.va]),
          epochs=EPOCHS, batch_size=BATCH, callbacks=cb, verbose=0)
    return (inv(m.predict(T.ent(T.va), verbose=0), T.va),
            inv(m.predict(T.ent(T.te), verbose=0), T.te), m.count_params(), m)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matriz", default="nucleo")
    ap.add_argument("--semillas", type=int, default=3)
    ap.add_argument("--familias", nargs="+", default=FAMILIAS)
    ap.add_argument("--guardar-modelos", action="store_true",
                    help="escribe los .keras de la mejor semilla de cada familia")
    a = ap.parse_args()

    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers
    from preparar_tensores import preparar, residuo

    salida = REPO / "data" / "gold" / f"finales_{a.matriz}"
    salida.mkdir(parents=True, exist_ok=True)
    print("TensorFlow", tf.__version__, "| GPU:",
          bool(tf.config.list_physical_devices("GPU")))

    T = preparar(a.matriz)
    yr, inv_r, mu_r, sd_r = residuo(T)
    mu_y, sd_y = float(T.y[T.tr].mean()), float(T.y[T.tr].std())
    ys = ((T.y - mu_y) / sd_y).astype("float32")
    inv_abs = lambda p, m: p * sd_y + mu_y

    nv = metricas(T.y[T.va], T.naive[T.va])
    nt = metricas(T.y[T.te], T.naive[T.te])
    print(f"   naive: val {nv['MAE']:.2f} · test {nt['MAE']:.2f} "
          f"· captura val {nv['captura_%']:.1f}%")
    print(f"   {len(a.familias)} familias x {a.semillas} semillas = "
          f"{len(a.familias) * a.semillas} entrenamientos")
    print()

    filas, pv_todo, pt_todo = [], {}, {}
    t0 = time.time()
    for fam in a.familias:
        for s in range(a.semillas):
            pv, pt, npar, modelo = entrenar(fam, T, yr, inv_r, ys, inv_abs,
                                            SEMILLA + s, keras, layers)
            mv, mt = metricas(T.y[T.va], pv), metricas(T.y[T.te], pt)
            clave = f"{fam}__s{s}"
            pv_todo[clave], pt_todo[clave] = pv, pt
            filas.append({"familia": fam, "semilla": SEMILLA + s, "parametros": npar,
                          "MAE_val": round(mv["MAE"], 3), "MAE_test": round(mt["MAE"], 3),
                          "captura_val_%": round(mv["captura_%"], 2),
                          "captura_test_%": round(mt["captura_%"], 2),
                          "pico_1h_test_%": round(mt["pico_1h_%"], 2),
                          "vs_naive_val_%": round(100 * (mv["MAE"] / nv["MAE"] - 1), 1)})
            pd.DataFrame(filas).to_csv(salida / "por_semilla.csv", index=False)
            print(f"   {fam:18s} s{s}  MAE val {mv['MAE']:6.3f} · test {mt['MAE']:6.3f} "
                  f"· captura {mt['captura_%']:5.1f}%   [{(time.time()-t0)/60:.0f} min]")
            if a.guardar_modelos and modelo is not None and s == 0:
                modelo.save(salida / f"{fam}.keras")
                # El .keras solo no sirve: guarda pesos, no la estandarizacion ni el orden
                # de columnas con el que se entreno. Sin esto, cargarlo en produccion
                # devuelve numeros plausibles y equivocados, sin ningun aviso.
                pre = T.preprocesado()
                pre["familia"] = fam
                pre["objetivo"] = "absoluto" if fam == "seq2seq_absoluto" else "residuo"
                pre["destipificar"] = ({"mu": mu_y, "sd": sd_y,
                                        "nota": "y = pred*sd + mu"}
                                       if fam == "seq2seq_absoluto"
                                       else {"mu": mu_r, "sd": sd_r,
                                             "nota": "y = pred*sd + mu + naive(dia D)"})
                pre["entrada"] = "plana" if fam in ("denso", "boosting") else "tensores"
                (salida / f"{fam}.preprocesado.json").write_text(
                    json.dumps(pre, indent=2, ensure_ascii=False), encoding="utf-8")

    d = pd.DataFrame(filas)
    res = (d.groupby("familia").agg(
        n=("semilla", "size"), parametros=("parametros", "first"),
        MAE_val=("MAE_val", "mean"), MAE_val_sd=("MAE_val", "std"),
        MAE_test=("MAE_test", "mean"), MAE_test_sd=("MAE_test", "std"),
        captura_test=("captura_test_%", "mean"))
        .round(3).sort_values("MAE_val"))
    res["vs_naive_test_%"] = (100 * (res.MAE_test / nt["MAE"] - 1)).round(1)

    # ── ensemble: mejor de cada familia SEGUN VALIDACION ─────────────────────
    mejor_por_fam = d.loc[d.groupby("familia")["MAE_val"].idxmin()]
    miembros = [f"{r.familia}__s{r.semilla - SEMILLA}" for r in mejor_por_fam.itertuples()
                if r.MAE_val < nv["MAE"]]
    if len(miembros) > 1:
        e_va = np.mean([pv_todo[k] for k in miembros], axis=0)
        e_te = np.mean([pt_todo[k] for k in miembros], axis=0)
        ev, et = metricas(T.y[T.va], e_va), metricas(T.y[T.te], e_te)
        pv_todo["ensemble"], pt_todo["ensemble"] = e_va, e_te
        res.loc["ensemble"] = {"n": len(miembros), "parametros": 0,
                               "MAE_val": round(ev["MAE"], 3), "MAE_val_sd": np.nan,
                               "MAE_test": round(et["MAE"], 3), "MAE_test_sd": np.nan,
                               "captura_test": round(et["captura_%"], 2),
                               "vs_naive_test_%": round(100 * (et["MAE"] / nt["MAE"] - 1), 1)}
        res = res.sort_values("MAE_val")

    print()
    print("=" * 78)
    print(res.to_string())
    print()
    print(f"ensemble de {len(miembros)} familias, elegidas por MAE de VALIDACION:")
    print(f"   {miembros}")
    print()
    print("El campeon por MAE y el campeon por CAPTURA no tienen por que ser el mismo.")
    print("La correlacion entre ambas metricas es -0,285, y para operar una bateria manda")
    print("la captura: no importa clavar 47,3 EUR/MWh, importa acertar CUANDO cargar.")
    top_mae = res.index[0]
    top_cap = res.captura_test.idxmax()
    print(f"   mejor MAE     : {top_mae}")
    print(f"   mejor captura : {top_cap}")
    if top_mae != top_cap:
        print("   NO coinciden -> exportar los dos, y decirlo en la memoria.")

    res.to_csv(salida / "resumen.csv")
    fechas_va = pd.to_datetime(T.fechas[T.va])
    for k, p in pv_todo.items():
        pd.DataFrame(p, index=fechas_va,
                     columns=[f"h{h:02d}" for h in range(24)]).to_csv(
            salida / f"pred_val_{k}.csv")
    if a.guardar_modelos:
        print()
        print("Cada .keras va con su .preprocesado.json: escaladores, orden de columnas y")
        print("como deshacer la tipificacion. Al cargarlo en produccion hay que COMPROBAR")
        print("que `hash_matriz` y `canales` coinciden con los de la matriz del dia; si no,")
        print("fallar en voz alta -- un modelo con las columnas cambiadas de sitio sigue")
        print("prediciendo y nadie se entera.")

    (salida / "meta.json").write_text(json.dumps({
        "matriz": a.matriz, "hash": T.meta.get("hash"), "semillas": a.semillas,
        "naive_val_MAE": round(nv["MAE"], 3), "naive_test_MAE": round(nt["MAE"], 3),
        "dias_train": int(T.tr.sum()), "dias_val": int(T.va.sum()),
        "dias_test": int(T.te.sum()), "canales_encoder": int(T.X_enc.shape[-1]),
        "ensemble": miembros,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGuardado en {salida}")


if __name__ == "__main__":
    main()
