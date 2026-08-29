"""
Calcula media y std por canal (de las 11 variables) usando ÚNICAMENTE
tensores del período de train (<= 2024-12), tal como fija el split
cronológico del proyecto. Estas estadísticas se guardan a disco y se
reusan para normalizar TODO el histórico (train+val+test) antes de
entrenar el autoencoder y, después, antes de generar embeddings.

Uso: correr en el VPS.
    python3 compute_train_stats.py
"""

import numpy as np
import glob
import os
import re
import json

TRAIN_END = (2024, 12)  # última tupla (año, mes) incluida en train

# Índice 9 = "tp" (precipitación, mm) en TENSOR_VAR_ORDER de era5_load.py:
# ["t2m", "d2m", "u10", "v10", "u100", "v100", "wind_gust10", "ssrd", "tcc",
#  "tp", "msl"]. Zero-inflada + cola larga -> log1p antes de estandarizar.
# Las stats guardadas (mean/std) quedan en espacio log1p para este canal;
# el Dataset debe aplicar el mismo log1p antes de normalizar (ver
# weather_tensor_dataset.py, LOG1P_CHANNELS).
LOG1P_CHANNELS = [9]


def month_key(filename: str):
    m = re.search(r"(\d{4})-(\d{2})\.npy$", filename)
    if not m:
        raise ValueError(f"No se pudo extraer año-mes de: {filename}")
    return int(m.group(1)), int(m.group(2))


def compute_train_stats(era5_dir: str, train_end=TRAIN_END, n_channels: int = 11):
    files = sorted(glob.glob(os.path.join(era5_dir, "era5_tensor_*.npy")))
    train_files = [f for f in files if month_key(f) <= train_end]
    print(
        f"Usando {len(train_files)} de {len(files)} archivos "
        f"(train <= {train_end[0]}-{train_end[1]:02d})"
    )
    if not train_files:
        raise RuntimeError("No se encontraron archivos de train — revisar TRAIN_END o la ruta.")

    count = 0
    sum_ = np.zeros(n_channels, dtype=np.float64)
    sumsq = np.zeros(n_channels, dtype=np.float64)

    for f in train_files:
        arr = np.load(f)  # (T, H, W, C)
        flat = arr.reshape(-1, arr.shape[-1]).astype(np.float64)
        for ch in LOG1P_CHANNELS:
            flat[:, ch] = np.log1p(flat[:, ch])
        count += flat.shape[0]
        sum_ += flat.sum(axis=0)
        sumsq += (flat ** 2).sum(axis=0)

    mean = sum_ / count
    var = np.maximum(sumsq / count - mean ** 2, 1e-12)  # evita std<=0 por error numérico
    std = np.sqrt(var)
    return mean, std, count


if __name__ == "__main__":
    home = os.path.expanduser("~")
    era5_dir = f"{home}/scripts/ingesta/tensors/era5"

    mean, std, n = compute_train_stats(era5_dir)

    print(f"\nPíxeles-tiempo totales usados: {n:,}")
    print(f"Media por canal: {np.round(mean, 4)}")
    print(f"Std por canal:   {np.round(std, 4)}")

    out_path = "era5_channel_stats.json"
    with open(out_path, "w") as fh:
        json.dump(
            {
                "mean": mean.tolist(),
                "std": std.tolist(),
                "n_samples": int(n),
                "log1p_channels": LOG1P_CHANNELS,
                "tensor_var_order": ["t2m", "d2m", "u10", "v10", "u100", "v100",
                                      "wind_gust10", "ssrd", "tcc", "tp", "msl"],
            },
            fh, indent=2,
        )
    print(f"\nGuardado en {out_path} (incluye log1p_channels y tensor_var_order para trazabilidad)")