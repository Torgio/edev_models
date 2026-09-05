"""
Fase C del generador de pseudo-tensores: arma el refs JSON en el mismo formato que
usa query_tensor_refs.py (listo para generate_embeddings.py sin ningún cambio), y
corre un chequeo de sanidad física básico sobre una muestra.

Requiere: pseudo_ecmwf_tensors/ (salida de la Fase B). Correr en el VPS.
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np

from generar_pseudo_tensores import INICIO, FIN, SALIDA_DIR, TENSOR_VAR_ORDER, _meses

OUTPUT_REFS = "pseudo_full_history_refs.json"

# Rangos físicos amplios para el chequeo de sanidad (unidades ya convertidas, ver
# era5_load.py). NO son los límites de RECORTES_TENSOR — son solo una alarma
# temprana si algo salió muy mal (no un criterio de recorte).
RANGOS_SANIDAD = {
    "t2m": (230, 330), "d2m": (220, 320), "ssrd": (0, 1400),
    "tcc": (0, 1), "tp": (0, 200), "msl": (90000, 108000),
}
_IDX = {v: i for i, v in enumerate(TENSOR_VAR_ORDER)}


def construir_refs(inicio=None, fin=None, salida_dir=None):
    inicio = inicio or INICIO
    fin = fin or FIN
    salida_dir = salida_dir or SALIDA_DIR
    refs = []
    for anio, mes in _meses(inicio, fin):
        ruta = os.path.join(salida_dir, f"pseudo_ecmwf_tensor_{anio}-{mes:02d}.npy")
        arr = np.load(ruta, mmap_mode="r")
        n_dias = arr.shape[0] // 8
        for dia in range(n_dias):
            for p in range(8):
                ts = datetime(anio, mes, dia + 1) + timedelta(hours=p * 3)
                refs.append({
                    "ts": ts.isoformat(),
                    "tensor_filename": f"pseudo_ecmwf_tensor_{anio}-{mes:02d}.npy",
                    "tensor_index": dia * 8 + p,
                })
    return refs


def guardar_refs(refs, path=None):
    """Escritura EXPLICITA a disco -- separada de construir_refs() a proposito.
    Antes, construir_refs() escribia sola a un nombre fijo (OUTPUT_REFS) en
    cada llamada, así que llamarla varias veces con rangos distintos (para
    combinar tramos) sobreescribia el archivo cada vez, silenciosamente."""
    path = path or OUTPUT_REFS
    with open(path, "w") as fh:
        json.dump(refs, fh)
    print(f"{path}: {len(refs)} filas")


def chequeo_sanidad(refs, salida_dir=None, n_muestras=5):
    salida_dir = salida_dir or SALIDA_DIR
    print(f"\nChequeo de sanidad sobre {n_muestras} muestras (repartidas en todo el rango):")
    idxs = np.linspace(0, len(refs) - 1, n_muestras, dtype=int)
    problemas = 0
    for i in idxs:
        r = refs[i]
        arr = np.load(os.path.join(salida_dir, r["tensor_filename"]), mmap_mode="r")
        tensor = np.array(arr[r["tensor_index"]])  # (33, 57, 11)
        tiene_nan = bool(np.isnan(tensor).any())
        fuera_de_rango = []
        for nombre, (lo, hi) in RANGOS_SANIDAD.items():
            v = tensor[..., _IDX[nombre]]
            if v.min() < lo or v.max() > hi:
                fuera_de_rango.append(f"{nombre}: [{v.min():.2f}, {v.max():.2f}] fuera de [{lo}, {hi}]")
        estado = "OK" if not tiene_nan and not fuera_de_rango else "REVISAR"
        if estado == "REVISAR":
            problemas += 1
        print(f"  [{r['ts']}] NaN={'sí' if tiene_nan else 'no'} "
              f"| fuera de rango: {fuera_de_rango or 'ninguno'} | {estado}")
    print(f"\n{problemas} de {n_muestras} muestras con algo para revisar.")
    return problemas


if __name__ == "__main__":
    refs = construir_refs()
    guardar_refs(refs)
    chequeo_sanidad(refs)
