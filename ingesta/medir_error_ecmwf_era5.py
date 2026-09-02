"""
Fase A del generador de pseudo-tensores: mide el error real ECMWF-vs-ERA5, tensor
completo (no escalar), dentro del solape de train (2024-04 a 2024-12-31 — nunca
val/test, misma disciplina que el resto del proyecto).

Generaliza medir_error()/_bloques_por_dia() de pseudo_prevision.py: en vez de un
número por variable, cada "bloque de error" es un array (8, 33, 57, 11) — un tensor
completo por cada una de las 8 marcas de 3h del día, para los días donde ERA5 y
ECMWF real coinciden en la MISMA hora exacta.

Por qué solo 8 marcas de 3h, no las 24 horas que tiene ECMWF real: ERA5 en este
período solo existe a grano de 3h — no hay forma de medir "el error a la 1am" si
ERA5 no tiene ningún dato a la 1am para comparar. El pseudo-tensor que se va a
construir con estos moldes también va a quedar a grano de 3h (heredado de ERA5,
la única fuente disponible antes de 2024-04), así que medir a ese mismo grano es
lo correcto, no una limitación.

Salida: una carpeta (SALIDA_DIR) con un .npy de error por día completo
(8, 33, 57, 11) y un índice JSON (moldes_error_index.json) listando qué días
quedaron disponibles y a qué mes pertenecen — para que generar_pseudo_tensores.py
(Fase B) pueda elegir "molde del mismo mes", igual que pseudo_prevision.py.

Requiere Postgres y ambas familias de tensores en disco — correr en el VPS.
"""

import json
import os
from collections import Counter, defaultdict

import numpy as np

from query_tensor_refs import get_tensor_refs, _safe_basename
from query_ecmwf_tensor_refs import get_ecmwf_refs

ERA5_BASE_DIR = os.path.expanduser("~") + "/scripts/ingesta/tensors/era5"
ECMWF_BASE_DIR = os.path.expanduser("~") + "/scripts/data/ecmwf_forecast_tensors"
SALIDA_DIR = "moldes_error_ecmwf"

INICIO_SOLAPE = "2024-04-01 00:00:00"
FIN_SOLAPE = "2024-12-31 23:59:59"  # nunca más allá de train — mismo corte de siempre


def _cargar_tensor(base_dir, raw_path, index):
    """raw_path viene de la DB tal cual se guardó -- puede ser absoluto de otro
    entorno (ver el bug de rutas mixtas que ya resolvimos). Se aplica el mismo
    basename robusto y se rearma contra base_dir, nunca se confía en el path crudo."""
    filename = _safe_basename(raw_path)
    path = os.path.join(base_dir, filename)
    arr = np.load(path, mmap_mode="r")
    return np.array(arr[index], dtype=np.float32)  # (33, 57, 11)


def procesar(era5_refs, ecmwf_refs, era5_base_dir=None, ecmwf_base_dir=None,
             salida_dir=None, verbose=True):
    """
    Núcleo testeable sin DB: recibe las refs ya consultadas (listas de tuplas) y
    hace el emparejamiento + bloques + guardado. era5_refs: [(ts, path, index), ...].
    ecmwf_refs: [(ts, path, index, fuente), ...].
    """
    era5_base_dir = era5_base_dir or ERA5_BASE_DIR
    ecmwf_base_dir = ecmwf_base_dir or ECMWF_BASE_DIR
    salida_dir = salida_dir or SALIDA_DIR

    era5_por_ts = {r[0]: (r[1], r[2]) for r in era5_refs}

    ecmwf_3h = [r for r in ecmwf_refs if r[0].hour % 3 == 0]
    if verbose:
        print(f"ECMWF en marcas de 3h dentro del solape: {len(ecmwf_3h)} de {len(ecmwf_refs)} horarias")

    pares = []
    for ts, path, index, fuente in ecmwf_3h:
        if ts in era5_por_ts:
            era5_path, era5_index = era5_por_ts[ts]
            pares.append((ts, path, index, era5_path, era5_index))
    if verbose:
        print(f"Pares ECMWF-ERA5 con ts exacto coincidente: {len(pares)}")
    if not pares:
        raise RuntimeError("No se encontró ningún ts coincidente entre ECMWF y ERA5 -- revisar refs")

    por_dia = defaultdict(dict)  # fecha -> {posicion_del_dia (0..7): tensor_error}
    for ts, ecmwf_path, ecmwf_idx, era5_path, era5_idx in pares:
        fecha = ts.date()
        pos_dia = ts.hour // 3
        ecmwf_t = _cargar_tensor(ecmwf_base_dir, ecmwf_path, ecmwf_idx)
        era5_t = _cargar_tensor(era5_base_dir, era5_path, era5_idx)
        por_dia[fecha][pos_dia] = ecmwf_t - era5_t

    os.makedirs(salida_dir, exist_ok=True)
    indice = []
    dias_incompletos = 0
    for fecha, posiciones in sorted(por_dia.items()):
        if len(posiciones) < 8:
            dias_incompletos += 1
            continue  # día incompleto, no sirve de molde -- igual que _bloques_por_dia
        bloque = np.stack([posiciones[i] for i in range(8)], axis=0)  # (8, 33, 57, 11)
        nombre = f"error_{fecha.isoformat()}.npy"
        np.save(os.path.join(salida_dir, nombre), bloque)
        indice.append({"fecha": fecha.isoformat(), "mes": fecha.month, "archivo": nombre})

    if verbose:
        print(f"\nDías completos guardados como molde: {len(indice)}")
        print(f"Días incompletos descartados: {dias_incompletos}")
        por_mes = Counter(d["mes"] for d in indice)
        print("Moldes disponibles por mes:")
        for m in sorted(por_mes):
            print(f"  mes {m:02d}: {por_mes[m]} días")

    with open(os.path.join(salida_dir, "moldes_error_index.json"), "w") as fh:
        json.dump(indice, fh, indent=1)
    if verbose:
        print(f"\nÍndice guardado en {salida_dir}/moldes_error_index.json")

    return indice


def main():
    import psycopg2
    from config import load_config

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    era5_refs = get_tensor_refs(conn, start_ts=INICIO_SOLAPE, end_ts=FIN_SOLAPE)
    ecmwf_refs = get_ecmwf_refs(conn, start_ts=INICIO_SOLAPE, end_ts=FIN_SOLAPE)
    conn.close()

    procesar(era5_refs, ecmwf_refs)


if __name__ == "__main__":
    main()
