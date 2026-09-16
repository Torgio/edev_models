"""
Diagnóstico de calidad del embedding meteorológico, antes de aceptar el
resultado negativo de la comparación de modelos como conclusión final.

Dos chequeos, ninguno requiere GPU:

  1. VARIANZA POR SPLIT: ¿las 32 columnas tensor_emb_ tienen varianza real,
     o alguna quedó casi constante? Una columna casi constante, tras la
     estandarización de preparar_tensores.py, se infla a varianza 1 --
     convirtiendo ruido numérico minúsculo en una "señal" que la red ve
     como si fuera información real. Se compara train/val/test por
     separado: si val/test tienen varianza mucho menor que train, apunta
     a degradación de calidad específica del pseudo-tensor.

  2. CORRELACIÓN CON EL CLIMA YA VALIDADO: ¿alguna de las 32 columnas
     correlaciona con los agregados escalares (*_meteo) que el equipo ya
     usa? Si NINGUNA correlaciona con nada, es señal de un problema en el
     pipeline (encoder, merge, o normalización) -- no evidencia de que
     "el clima no importa", porque seguiría siendo el mismo clima medido
     de otra forma.

Uso: correr sobre matriz_nucleo_tensores.parquet.
"""

import numpy as np
import pandas as pd

PREFIJO_EMBEDDING = "tensor_emb_"
UMBRAL_VARIANZA_SOSPECHOSA = 0.01  # std por debajo de esto, en unidades originales, es sospechoso
UMBRAL_CORRELACION_INTERESANTE = 0.15


def chequeo_varianza(df, verbose=True):
    cols_emb = [c for c in df.columns if c.startswith(PREFIJO_EMBEDDING)]
    if not cols_emb:
        raise RuntimeError("No se encontraron columnas tensor_emb_ -- ¿es la matriz correcta?")

    resultado = {}
    for split in ["train", "validation", "test"]:
        sub = df[df["split"] == split]
        stds = sub[cols_emb].std()
        resultado[split] = stds

    tabla = pd.DataFrame(resultado)
    sospechosas = tabla[(tabla < UMBRAL_VARIANZA_SOSPECHOSA).any(axis=1)]

    if verbose:
        print(f"Varianza (std) de las {len(cols_emb)} columnas tensor_emb_, por split:\n")
        print(tabla.round(4).to_string())
        print(f"\n{len(sospechosas)} columna(s) con std < {UMBRAL_VARIANZA_SOSPECHOSA} en algún split:")
        if len(sospechosas):
            print(sospechosas.round(4).to_string())
        else:
            print("  ninguna -- todas las columnas tienen varianza real en los tres splits")

        # Comparacion directa: cuanto cae la varianza de train a val/test
        ratio_val = (tabla["validation"] / tabla["train"]).median()
        ratio_test = (tabla["test"] / tabla["train"]).median()
        print(f"\nRatio mediano de std (val/train): {ratio_val:.3f}")
        print(f"Ratio mediano de std (test/train): {ratio_test:.3f}")
        print("(cerca de 1.0 = varianza similar; muy por debajo de 1 sugiere degradacion")
        print(" de calidad del embedding en val/test, coherente con el uso de pseudo-tensor)")

    return tabla


def chequeo_correlacion_meteo(df, verbose=True):
    cols_emb = [c for c in df.columns if c.startswith(PREFIJO_EMBEDDING)]
    cols_meteo = [c for c in df.columns if c.endswith("_meteo")]
    if not cols_meteo:
        raise RuntimeError("No se encontraron columnas *_meteo -- ¿es la matriz correcta?")

    sub = df[df["split"] == "train"]  # correlacion medida solo en train, disciplina de siempre
    correlaciones = pd.DataFrame(
        {m: [sub[e].corr(sub[m]) for e in cols_emb] for m in cols_meteo},
        index=cols_emb,
    )

    max_abs_por_emb = correlaciones.abs().max(axis=1)
    interesantes = max_abs_por_emb[max_abs_por_emb > UMBRAL_CORRELACION_INTERESANTE]

    if verbose:
        print(f"\nCorrelación máxima (valor absoluto) de cada tensor_emb_ contra "
              f"cualquier columna *_meteo (medido en train):\n")
        print(max_abs_por_emb.round(3).sort_values(ascending=False).to_string())
        print(f"\n{len(interesantes)} de {len(cols_emb)} columnas tensor_emb_ con "
              f"correlación > {UMBRAL_CORRELACION_INTERESANTE} con algún *_meteo")
        if len(interesantes) == 0:
            print("  AVISO: ninguna columna del embedding correlaciona con el clima ya "
                  "validado -- revisar el pipeline (encoder, merge_asof, normalización) "
                  "antes de aceptar el resultado como 'el clima no importa'")
        else:
            print("  Al menos algunas columnas SI capturan clima real conocido -- el "
                  "pipeline parece estar midiendo clima genuino, aunque no necesariamente "
                  "informacion util para el precio")

    return correlaciones


if __name__ == "__main__":
    df = pd.read_parquet("/home/ubuntu/scripts/data/gold/matriz_nucleo_tensores.parquet")
    chequeo_varianza(df)
    chequeo_correlacion_meteo(df)
