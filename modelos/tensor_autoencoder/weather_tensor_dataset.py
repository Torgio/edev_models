"""
Dataset de PyTorch para tensores meteorológicos. Cada muestra corresponde
a un único timestep (H=33, W=57, C=11), localizado mediante
(tensor_path, tensor_index) — las mismas columnas que ya existen en
era5_weather_agg / ecmwf_forecast_agg. La lista de (tensor_path,
tensor_index) para train/val/test se arma consultando esas tablas y
filtrando por fecha según el split cronológico del proyecto — eso queda
fuera de este archivo, que solo se ocupa de la carga eficiente.

Usa np.load(..., mmap_mode="r") con caché por archivo: como muchas
muestras consecutivas suelen caer en el mismo .npy mensual, evita
reabrir y releer el mes completo en cada __getitem__.
"""

import json
import numpy as np
import torch
from torch.utils.data import Dataset


class WeatherTensorDataset(Dataset):
    def __init__(self, tensor_paths, tensor_indices, channel_mean, channel_std,
                 log1p_channels=(9,)):
        # log1p_channels default = (9,) -> "tp" en TENSOR_VAR_ORDER de
        # era5_load.py. Debe coincidir EXACTO con lo usado al calcular
        # channel_mean/channel_std en compute_train_stats.py, o las stats
        # quedan desalineadas con los datos que efectivamente ve el modelo.
        assert len(tensor_paths) == len(tensor_indices), (
            "tensor_paths y tensor_indices deben tener la misma longitud"
        )
        self.tensor_paths = list(tensor_paths)
        self.tensor_indices = list(tensor_indices)
        self.log1p_channels = list(log1p_channels)
        # reshape (1,1,C) para broadcast directo sobre (H,W,C) channels-last
        self.mean = np.asarray(channel_mean, dtype=np.float32).reshape(1, 1, -1)
        self.std = np.asarray(channel_std, dtype=np.float32).reshape(1, 1, -1)
        self._mmap_cache = {}

    def __len__(self):
        return len(self.tensor_paths)

    def _get_mmap(self, path: str):
        if path not in self._mmap_cache:
            self._mmap_cache[path] = np.load(path, mmap_mode="r")
        return self._mmap_cache[path]

    def __getitem__(self, idx: int) -> torch.Tensor:
        path = self.tensor_paths[idx]
        t_idx = self.tensor_indices[idx]
        arr = self._get_mmap(path)
        sample = np.array(arr[t_idx]).astype(np.float32)  # copia real, saca la vista del mmap: (H, W, C)
        for ch in self.log1p_channels:
            sample[:, :, ch] = np.log1p(sample[:, :, ch])
        sample = (sample - self.mean) / self.std
        sample = np.transpose(sample, (2, 0, 1))  # channels-last -> channels-first
        return torch.from_numpy(sample).float()

    @classmethod
    def from_stats_file(cls, tensor_paths, tensor_indices, stats_path: str):
        """Conveniencia: carga mean/std/log1p_channels directo del JSON de compute_train_stats.py"""
        with open(stats_path) as fh:
            stats = json.load(fh)
        return cls(
            tensor_paths, tensor_indices, stats["mean"], stats["std"],
            log1p_channels=stats.get("log1p_channels", (9,)),
        )


if __name__ == "__main__":
    # Sanity check con datos sintéticos (no requiere los .npy reales)
    import tempfile, os

    tmpdir = tempfile.mkdtemp()
    fake_path = os.path.join(tmpdir, "fake_month.npy")
    fake_data = np.random.randn(10, 33, 57, 11).astype(np.float32)
    fake_data[:, :, :, 9] = np.abs(fake_data[:, :, :, 9])  # tp (canal 9) siempre >= 0
    np.save(fake_path, fake_data)

    mean = np.zeros(11)
    std = np.ones(11)
    ds = WeatherTensorDataset(
        tensor_paths=[fake_path] * 5,
        tensor_indices=[0, 1, 2, 3, 4],
        channel_mean=mean,
        channel_std=std,
    )
    sample = ds[0]
    print(f"Dataset len: {len(ds)}")
    print(f"Sample shape: {sample.shape}, dtype: {sample.dtype}")
    assert sample.shape == (11, 33, 57)
    print("OK: shape channels-first correcta")