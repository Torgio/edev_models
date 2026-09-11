"""
Compara TRES configuraciones, entrenando las 8 familias x 3 semillas de
entrenar_finales.py sobre cada una:

  1. sin_embedding               -- matriz_nucleo original, sin ningun parche.
  2. con_embedding_neutralizado  -- matriz_nucleo_tensores, con el embedding
                                     meteorologico agregado Y es_esios_D/
                                     pt_entsoe_D anuladas en X_dec (default de
                                     preparar_con_embeddings.py).
  3. con_embedding_completo      -- igual que 2, pero SIN anular esas dos
                                     columnas (neutralizar_dominantes=False).

Las tres, para poder aislar UNA variable a la vez en la comparacion:
  - (1) vs (3): ambas con es_esios_D intacta -- la unica diferencia real es
    el embedding. Esta es la comparacion que responde si el clima aporta algo.
  - (3) vs (2): ambas con el embedding presente -- la unica diferencia es si
    es_esios_D/pt_entsoe_D estan neutralizadas. Aisla el costo de neutralizarlas,
    por separado del efecto del embedding.
  - (1) vs (2) mezcla ambos efectos a la vez -- no aisla nada, evitar sacar
    conclusiones de esta comparacion sola.

AISLAMIENTO: este script IMPORTA preparar_tensores.py y entrenar_finales.py
tal cual están — nunca los modifica, ni copia su lógica. Si el equipo los
actualiza, este script hereda esa actualización automáticamente, sin que
tengamos que sincronizar nada a mano.

Requiere: TensorFlow/Keras (Colab, GPU) — no hay GPU local disponible.
Estructura de carpetas esperada (la exige preparar_tensores.py internamente,
via REPO = Path(__file__).resolve().parent.parent):

    <REPO>/scripts/preparar_tensores.py
    <REPO>/scripts/entrenar_finales.py
    <REPO>/data/gold/matriz_nucleo.parquet (+ .meta.json)
    <REPO>/data/gold/matriz_nucleo_tensores.parquet (+ .meta.json)

Este archivo y preparar_con_embeddings.py pueden vivir en cualquier carpeta
agregada al sys.path -- no necesitan seguir esa estructura.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

SALIDA = Path("comparacion_embeddings")
SEMILLAS = 3


def correr_experimento(nombre, T, EF, keras, layers, salida=SALIDA, verbose=True):
    """Corre las familias de EF.FAMILIAS x SEMILLAS sobre T, usando
    EF.entrenar()/EF.metricas() sin modificarlos. Devuelve un DataFrame."""
    from preparar_tensores import residuo

    salida.mkdir(parents=True, exist_ok=True)
    yr, inv_r, mu_r, sd_r = residuo(T)
    mu_y, sd_y = float(T.y[T.tr].mean()), float(T.y[T.tr].std())
    ys = ((T.y - mu_y) / sd_y).astype("float32")
    inv_abs = lambda p, m: p * sd_y + mu_y

    nv = EF.metricas(T.y[T.va], T.naive[T.va])
    nt = EF.metricas(T.y[T.te], T.naive[T.te])
    if verbose:
        print(f"[{nombre}] naive: val {nv['MAE']:.2f} · test {nt['MAE']:.2f}")

    csv = salida / f"por_semilla_{nombre}.csv"
    filas = pd.read_csv(csv).to_dict("records") if csv.exists() else []
    yahay = {(r["familia"], r["semilla"]) for r in filas}

    t0 = time.time()
    for fam in EF.FAMILIAS:
        for s in range(SEMILLAS):
            if (fam, EF.SEMILLA + s) in yahay:
                continue
            pv, pt, npar, modelo = EF.entrenar(fam, T, yr, inv_r, ys, inv_abs,
                                               EF.SEMILLA + s, keras, layers)
            mv, mt = EF.metricas(T.y[T.va], pv), EF.metricas(T.y[T.te], pt)
            filas.append({
                "experimento": nombre, "familia": fam, "semilla": EF.SEMILLA + s,
                "parametros": npar,
                "MAE_val": round(mv["MAE"], 3), "MAE_test": round(mt["MAE"], 3),
                "captura_val_%": round(mv["captura_%"], 2),
                "captura_test_%": round(mt["captura_%"], 2),
                "pico_1h_test_%": round(mt["pico_1h_%"], 2),
                "vs_naive_test_%": round(100 * (mt["MAE"] / nt["MAE"] - 1), 1),
            })
            pd.DataFrame(filas).to_csv(csv, index=False)
            if verbose:
                print(f"   [{nombre}] {fam:18s} s{s}  MAE val {mv['MAE']:6.3f} · "
                      f"test {mt['MAE']:6.3f} · captura {mt['captura_%']:5.1f}%   "
                      f"[{(time.time()-t0)/60:.1f} min]")

    return pd.DataFrame(filas)


def main(salida=SALIDA):
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers
    from preparar_tensores import preparar
    import entrenar_finales as EF
    from preparar_con_embeddings import preparar_con_embeddings

    print("TensorFlow", tf.__version__, "| GPU:", bool(tf.config.list_physical_devices("GPU")))

    print("\n=== Experimento SIN embedding (matriz_nucleo original) ===")
    T_sin = preparar("nucleo")
    df_sin = correr_experimento("sin_embedding", T_sin, EF, keras, layers, salida)

    print("\n=== Experimento CON embedding + es_esios_D/pt_entsoe_D NEUTRALIZADAS ===")
    T_con_neutr = preparar_con_embeddings(matriz="nucleo_tensores")  # default: neutralizar_dominantes=True
    df_con_neutr = correr_experimento("con_embedding_neutralizado", T_con_neutr, EF, keras, layers, salida)

    print("\n=== Experimento CON embedding + es_esios_D/pt_entsoe_D COMPLETAS ===")
    T_con_completo = preparar_con_embeddings(matriz="nucleo_tensores", neutralizar_dominantes=False)
    df_con_completo = correr_experimento("con_embedding_completo", T_con_completo, EF, keras, layers, salida)

    combinado = pd.concat([df_sin, df_con_neutr, df_con_completo], ignore_index=True)
    combinado.to_csv(salida / "comparacion_completa.csv", index=False)

    resumen = combinado.groupby(["experimento", "familia"]).agg(
        MAE_val=("MAE_val", "mean"), MAE_val_sd=("MAE_val", "std"),
        MAE_test=("MAE_test", "mean"), MAE_test_sd=("MAE_test", "std"),
        captura_test=("captura_test_%", "mean"),
    ).round(3)
    print("\n" + "=" * 78)
    print(resumen.to_string())
    resumen.to_csv(salida / "resumen_comparacion.csv")
    return combinado


if __name__ == "__main__":
    main()