"""
Genera matriz_nucleo_tensores.meta.json, siguiendo el esquema REAL de
matriz_nucleo.meta.json (leído como plantilla) — no un formato inventado.

Separación explícita en dos categorías, para no mezclar hecho con juicio:

  CAMPOS COMPUTADOS: se leen directo del parquet real (hash, filas, columnas,
  reparto, ventana, nulos) — verificables, no involucran ninguna suposición.

  CAMPOS EDITORIALES: una propuesta razonable basada en todo lo discutido en
  la bitácora, pero involucran juicio (nombre, aisla, origen,
  bloque_generacion, extensión de meteorologia, excluidas_por_fuga_target).
  Revisarlos antes de darlos por definitivos.

  n_inputs: se ajusta por DELTA desde el valor original (113 - 2 excluidas +
  32 tensor_emb_ = 143), NO recalculando una regla completa de "qué cuenta
  como input" — no conocemos esa regla con certeza. Asume que
  es_esios_D/pt_entsoe_D contaban como input y que tensor_emb_* debe
  contarlo también. Razonable, pero no verificado contra una definición
  explícita del equipo.

  origenes: NO se ajusta automáticamente — restar 2 de la categoría correcta
  requeriría saber con certeza en cuál de las categorías del desglose caían
  es_esios_D/pt_entsoe_D (probablemente "spot_price (Europa, dia D)": 5, pero
  es una suposición). Se deja igual al original, con un aviso explícito de
  que quedó desactualizado y hay que revisarlo a mano.

  catalogo_columnas: no se genera un equivalente a matriz_nucleo_columnas.csv
  — queda como tarea pendiente, no bloqueante.
"""

import datetime
import hashlib
import json
from pathlib import Path

import pandas as pd

ORIGINAL_MATRIZ = Path("/home/ubuntu/scripts/data/gold/matriz_nucleo.parquet")
ORIGINAL_META = Path("/home/ubuntu/scripts/data/gold/matriz_nucleo.meta.json")
NUEVA_MATRIZ = Path("/home/ubuntu/scripts/data/gold/matriz_nucleo_tensores.parquet")
SALIDA = Path("/home/ubuntu/scripts/data/gold/matriz_nucleo_tensores.meta.json")

PREFIJO_AGREGADAS = "tensor_emb_"


def _hash_archivo(path, n_hex=8):
    """Fingerprint del contenido real del archivo -- cambia si el archivo
    cambia. No pretende replicar el algoritmo exacto que usa el equipo (no
    lo conocemos), pero cumple la misma función: detectar si dos versiones
    difieren."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()[:n_hex]


def generar_meta(original_meta=ORIGINAL_META, original_matriz=ORIGINAL_MATRIZ,
                  nueva_matriz=NUEVA_MATRIZ, salida=SALIDA, verbose=True):
    with open(original_meta, encoding="utf-8") as fh:
        meta = json.load(fh)

    df = pd.read_parquet(nueva_matriz)
    df_original_cols = pd.read_parquet(original_matriz, columns=[]).columns  # solo el esquema, liviano
    if len(df_original_cols) == 0:
        df_original_cols = pd.read_parquet(original_matriz).columns  # fallback si el engine no soporta columns=[]

    n_agregadas = sum(1 for c in df.columns if c.startswith(PREFIJO_AGREGADAS))
    # Excluidas = lo que estaba en la matriz original y NO esta en la nueva --
    # calculado del dato real, no de una lista hardcodeada que puede
    # desincronizarse de construir_matriz_tensores.py (ya paso una vez).
    columnas_excluidas_real = sorted(set(df_original_cols) - set(df.columns))

    # ── Campos COMPUTADOS: leídos directo del archivo real ──────────────────
    meta["hash"] = _hash_archivo(nueva_matriz)
    meta["generada"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta["filas"] = len(df)
    meta["columnas"] = len(df.columns)
    meta["reparto"] = {k: int(v) for k, v in df["split"].value_counts().to_dict().items()}
    fo = pd.to_datetime(df["fecha_objetivo"])
    meta["ventana"] = f"{fo.min().date()} -> {fo.max().date()}"
    meta["nulos"] = int(df.isna().sum().sum())

    # ── Delta verificable, no una regla completa recalculada ────────────────
    n_inputs_original = meta.get("n_inputs", 0)
    meta["n_inputs"] = n_inputs_original - len(columnas_excluidas_real) + n_agregadas

    # ── Campos EDITORIALES: propuesta, revisar antes de dar por definitivos ─
    meta["nombre"] = "nucleo_tensores"
    meta["aisla"] = ("columnas (¿aporta el embedding meteorológico espacial "
                      "sobre los agregados escalares existentes?)")
    meta["origen"] = ("matriz_nucleo.parquet + merge_asof(backward) con embeddings de un "
                       "autoencoder CNN congelado (ERA5 / pseudo-tensor / ECMWF real) -- "
                       "ver bitacora_pipeline_embeddings.md")
    meta["bloque_generacion"] = (
        "Embeddings de tensores meteorologicos 33x57x11 -> vector K=32 via autoencoder "
        "CNN congelado, entrenado solo con ERA5 de train. ERA5+error-inyectado (pseudo) "
        "donde no hay ECMWF real archivado (2020-2024.03, y extendido a 2025-04/2025-12 "
        "y 2026-01/2026-07 por restriccion de tiempo del TFM, no por ausencia permanente "
        "de la fuente); ECMWF real (Open-Meteo previous-runs) donde existe"
    )
    meta["meteorologia"] = (
        meta.get("meteorologia", "") +
        " | tensor_emb_0..31: mismo empalme real/pseudo que *_meteo, pero a nivel de "
        "tensor espacial completo (33x57 grilla), no promedio escalar sobre la peninsula"
    )
    if columnas_excluidas_real:
        meta["excluidas_de_la_matriz"] = {
            "columnas": columnas_excluidas_real,
            "motivo": ("ver construir_matriz_tensores.py (COLUMNAS_EXCLUIR) para el motivo "
                       "exacto -- no se repite aca para evitar que las dos copias del motivo "
                       "se desincronicen, como ya paso una vez con este mismo campo")
        }
    else:
        meta.pop("excluidas_de_la_matriz", None)
        meta.pop("excluidas_por_fuga_target", None)  # limpia el campo viejo si venia de una corrida anterior

    if verbose:
        print("Campos COMPUTADOS (verificables contra el archivo real):")
        for k in ["hash", "generada", "filas", "columnas", "reparto", "ventana", "nulos"]:
            print(f"  {k}: {meta[k]}")
        print(f"\nn_inputs (por delta: {n_inputs_original} - {len(columnas_excluidas_real)} + "
              f"{n_agregadas} = {meta['n_inputs']}) -- no es un recalculo completo de la regla")
        print("\nCampos EDITORIALES (propuesta -- revisar antes de dar por definitivos):")
        for k in ["nombre", "aisla", "origen"]:
            print(f"  {k}: {meta[k]}")
        if "excluidas_de_la_matriz" in meta:
            print(f"  excluidas_de_la_matriz: {meta['excluidas_de_la_matriz']}")
        else:
            print("  (ninguna columna excluida de la matriz esta vez)")
        print("\nAVISO: 'origenes' (desglose por tabla fuente) quedó SIN TOCAR -- ya no "
              "refleja la resta de es_esios_D/pt_entsoe_D ni el alta de tensor_emb_*. "
              "Revisar a mano si ese desglose importa para la memoria.")
        print("AVISO: no se generó equivalente a matriz_nucleo_columnas.csv (catalogo_columnas) "
              "-- pendiente, no bloqueante.")

    with open(salida, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    if verbose:
        print(f"\nGuardado en {salida}")
    return meta


if __name__ == "__main__":
    generar_meta()
