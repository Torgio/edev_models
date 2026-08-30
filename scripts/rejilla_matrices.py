"""Fase A: que matriz gana, con varias semillas para que la respuesta se sostenga.

LA PREGUNTA
Las cuatro matrices existen para contestar tres cosas, y ninguna esta contestada: solo
`nucleo` se ha probado sobre la version actual.

    completa vs nucleo    ¿sobran variables?
    minima   vs nucleo    ¿bastan las 25 mejores?
    moderna  vs nucleo    ¿estorba la crisis del gas de 2020-2022?

POR QUE TRES ARQUITECTURAS Y NO UNA
Porque el tamaño de la matriz interactua con el tamaño del modelo, y quedarse con la mejor
de `nucleo` sesgaria el resultado. `moderna` entrena con 17.566 filas frente a 43.699 -- el
40 % -- y `minima` tiene 10 canales de encoder frente a 51. Con menos datos, los modelos
pequeños suben posiciones. Es perfectamente posible que el SimpleRNN, sexto en `nucleo`,
gane en `moderna`, y con una sola arquitectura no se veria.

Asi que se cubre el espectro de tamaños:

    SimpleRNN      ~35 k parametros
    GRU           ~320 k
    Conv1D+LSTM   ~460 k

POR QUE VARIAS SEMILLAS
Porque la desviacion entre semillas del MISMO modelo es de ~0,3 MAE, y las diferencias que
se quieren medir son de ese orden. Con una sola semilla, el ranking es en buena parte azar.
Cada celda de la rejilla sale con media y desviacion.

Se escribe el CSV despues de cada entrenamiento, asi que una interrupcion no pierde lo ya
hecho y relanzar continua donde estaba.

Uso:
    python scripts/rejilla_matrices.py                       # 3 arq x 4 matrices x 3 semillas
    python scripts/rejilla_matrices.py --semillas 1          # barrido rapido, 40 min
    python scripts/rejilla_matrices.py --matrices nucleo moderna
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))

SALIDA = REPO / "data" / "gold" / "rejilla_matrices.csv"
MATRICES = ["completa", "nucleo", "minima", "moderna"]
SEMILLA = 42
EPOCHS, BATCH = 80, 64


def metricas(yt, yp):
    e = yp - yt
    den = (np.abs(yt) + np.abs(yp)) / 2
    n = np.arange(len(yt))
    # captura de spread: que fraccion del arbitraje perfecto se obtiene cargando en el valle
    # PREDICHO y descargando en el pico PREDICHO. El MAE mide precision; esto mide dinero.
    cap = ((yt[n, yp.argmax(1)] - yt[n, yp.argmin(1)]) /
           np.maximum(yt.max(1) - yt.min(1), .1))
    return {"MAE": float(np.abs(e).mean()),
            "RMSE": float(np.sqrt((e ** 2).mean())),
            "sMAPE": float(100 * np.mean(np.abs(e) / np.maximum(den, 1e-3))),
            "captura_%": float(100 * cap.mean()),
            "pico_1h_%": float(100 * (np.abs(yt.argmax(1) - yp.argmax(1)) <= 1).mean())}


def construir(arq, T, keras, layers):
    """Las tres arquitecturas, sobre el mismo esqueleto encoder-decoder."""
    ie = keras.Input(shape=T.X_enc.shape[1:], name="hist")
    idc = keras.Input(shape=T.X_dec.shape[1:], name="fut")
    ist = keras.Input(shape=(T.X_est.shape[1],), name="est")
    dr = 0.3

    if arq == "SimpleRNN":
        u, celda, x = 64, layers.SimpleRNN, ie
    elif arq == "GRU":
        u, celda, x = 96, layers.GRU, ie
    elif arq == "Conv1D+LSTM":
        u, celda = 96, layers.LSTM
        # el Conv1D con stride comprime 168 pasos a 42 extrayendo el patron local -- la
        # rampa solar, el pico de tarde -- y deja a la recurrente la dinamica de medio plazo
        x = layers.Conv1D(64, 5, strides=4, padding="causal", activation="relu")(ie)
        x = layers.BatchNormalization()(x)
    else:
        raise ValueError(arq)

    enc = layers.Bidirectional(celda(u, dropout=dr))(x)
    ctx = layers.Concatenate()([enc, layers.Dense(64, activation="relu")(ist)])
    dec = layers.Concatenate()([layers.RepeatVector(24)(ctx), idc])
    dec = celda(u, return_sequences=True, dropout=dr)(dec)
    out = layers.Reshape((24,))(layers.TimeDistributed(layers.Dense(1))(dec))
    m = keras.Model([ie, idc, ist], out, name=arq.replace("+", "_"))
    m.compile(optimizer=keras.optimizers.Adam(1e-3),
              loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--semillas", type=int, default=3)
    ap.add_argument("--matrices", nargs="+", default=MATRICES)
    ap.add_argument("--arquitecturas", nargs="+",
                    default=["SimpleRNN", "GRU", "Conv1D+LSTM"])
    a = ap.parse_args()

    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers
    from preparar_tensores import preparar, residuo

    print("TensorFlow", tf.__version__, "| GPU:",
          bool(tf.config.list_physical_devices("GPU")))
    total = len(a.matrices) * len(a.arquitecturas) * a.semillas
    print(f"{total} entrenamientos: {len(a.matrices)} matrices x "
          f"{len(a.arquitecturas)} arquitecturas x {a.semillas} semillas")
    print()

    hechos = []
    if SALIDA.exists():
        hechos = pd.read_csv(SALIDA).to_dict("records")
        print(f"Continuando: {len(hechos)} filas ya en {SALIDA.name}")
    yahay = {(r["matriz"], r["arquitectura"], r["semilla"]) for r in hechos}

    t0 = time.time()
    for matriz in a.matrices:
        T = preparar(matriz)
        yr, inv_r, _, _ = residuo(T)
        mae_naive_va = float(metricas(T.y[T.va], T.naive[T.va])["MAE"])
        mae_naive_te = float(metricas(T.y[T.te], T.naive[T.te])["MAE"])
        print(f"   naive: val {mae_naive_va:.2f} · test {mae_naive_te:.2f}")

        for arq in a.arquitecturas:
            for s in range(a.semillas):
                if (matriz, arq, SEMILLA + s) in yahay:
                    continue
                keras.utils.set_random_seed(SEMILLA + s)
                m = construir(arq, T, keras, layers)
                m.fit(T.ent(T.tr), yr[T.tr],
                      validation_data=(T.ent(T.va), yr[T.va]),
                      epochs=EPOCHS, batch_size=BATCH, verbose=0,
                      callbacks=[keras.callbacks.EarlyStopping(
                                     monitor="val_loss", patience=12,
                                     restore_best_weights=True),
                                 keras.callbacks.ReduceLROnPlateau(
                                     monitor="val_loss", factor=0.5, patience=5,
                                     min_lr=1e-5)])
                mv = metricas(T.y[T.va], inv_r(m.predict(T.ent(T.va), verbose=0), T.va))
                mt = metricas(T.y[T.te], inv_r(m.predict(T.ent(T.te), verbose=0), T.te))
                hechos.append({
                    "matriz": matriz, "hash": T.meta.get("hash", "?"),
                    "arquitectura": arq, "semilla": SEMILLA + s,
                    "parametros": int(m.count_params()),
                    "dias_train": int(T.tr.sum()), "canales": T.X_enc.shape[-1],
                    "MAE_val": round(mv["MAE"], 3), "MAE_test": round(mt["MAE"], 3),
                    "captura_val_%": round(mv["captura_%"], 2),
                    "vs_naive_val_%": round(100 * (mv["MAE"] / mae_naive_va - 1), 1),
                    "naive_val": round(mae_naive_va, 3),
                })
                pd.DataFrame(hechos).to_csv(SALIDA, index=False)
                r = hechos[-1]
                print(f"   {arq:12s} s{s}  MAE val {r['MAE_val']:6.3f} "
                      f"({r['vs_naive_val_%']:+5.1f}% vs naive) · test {r['MAE_test']:6.3f}"
                      f"   [{(time.time()-t0)/60:.0f} min]")
        print()

    d = pd.DataFrame(hechos)
    print("=" * 74)
    print("MEDIA +- DESVIACION del MAE de validacion, por matriz y arquitectura:")
    piv = d.pivot_table(index="arquitectura", columns="matriz", values="MAE_val",
                        aggfunc=["mean", "std"]).round(3)
    print(piv.to_string())
    print()
    print("vs naive en validacion (%), que descuenta la dificultad propia de cada ventana:")
    print(d.pivot_table(index="arquitectura", columns="matriz",
                        values="vs_naive_val_%", aggfunc="mean").round(1).to_string())
    print()
    print("COMO LEERLO. Las cuatro matrices validan sobre el MISMO periodo -- 2025 entero --")
    print("asi que el MAE es directamente comparable entre columnas. Lo que cambia es con")
    print("cuantos dias entrena cada una: `moderna` con 17.566 filas frente a las 43.699 de")
    print("las demas. Si `moderna` empata, esta ganando: consigue lo mismo con el 40 % de")
    print("los datos. Y si una arquitectura cambia de posicion entre columnas, ahi esta la")
    print("interaccion entre tamano de matriz y tamano de modelo que motiva este barrido.")

    # ── comparacion PAREADA por semilla ───────────────────────────────────────
    # Comparar medias sueltas desperdicia el diseno: las cuatro matrices se entrenan con
    # LAS MISMAS semillas, asi que restando cada matriz contra otra en la MISMA semilla y
    # arquitectura desaparece la varianza de inicializacion, que es la que ensucia todo.
    #
    # Con medias sueltas hacen falta diferencias de ~0,5 para distinguir algo; pareado
    # basta con ~0,15. Y el criterio se lee sin estadistica: si las diferencias tienen
    # todas el mismo signo y superan su propia dispersion, hay ganador; si cambian de
    # signo, lo que se esta midiendo es ruido.
    print()
    print("=" * 74)
    print("COMPARACION PAREADA (cada matriz contra `nucleo`, misma semilla y arquitectura)")
    ref = "nucleo"
    if ref in set(d.matriz):
        base = d[d.matriz == ref].set_index(["arquitectura", "semilla"])["MAE_val"]
        filas = []
        for m in [x for x in d.matriz.unique() if x != ref]:
            otra = d[d.matriz == m].set_index(["arquitectura", "semilla"])["MAE_val"]
            dif = (otra - base).dropna()
            if not len(dif):
                continue
            mismo = bool((dif > 0).all() or (dif < 0).all())
            filas.append({
                "matriz": m, "n_pares": len(dif),
                "dif_media": round(float(dif.mean()), 3),
                "dif_sd": round(float(dif.std(ddof=1)), 3) if len(dif) > 1 else None,
                "mismo_signo": mismo,
                "veredicto": (f"gana {ref}" if dif.mean() > 0 else f"gana {m}")
                             if mismo and abs(dif.mean()) > 0.15 else "empate",
            })
        if filas:
            print(pd.DataFrame(filas).to_string(index=False))
            print()
            print(f"`dif_media` positiva significa PEOR que {ref}.")
            print("`mismo_signo` es la clave: si el signo cambia entre semillas, se esta")
            print("midiendo ruido y no la matriz.")
            print()
            print("Si empata con `completa`, gana `nucleo`: mismo resultado con 113 inputs")
            print("en vez de 141. Si el empate es con `moderna`, es un hallazgo -- consigue")
            print("lo mismo con el 40 % de los dias, o sea que 2020-2022 no aportan.")
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
