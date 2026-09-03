"""
Loop de entrenamiento del autoencoder CNN. Pensado para correr en Colab (GPU),
NO en el VPS (CPU-only) ni en el sandbox de Claude (sin acceso a los datos).

Archivos que este script espera encontrar ya sincronizados vía Drive/SFTP
(ver flujo de acceso a datos ya establecido en el proyecto):
  - internal_train_refs.json, internal_val_refs.json  (de query_tensor_refs.py;
    contienen tensor_filename, NO una ruta completa — son portables)
  - era5_channel_stats.json                            (de compute_train_stats.py)
  - los .npy de ERA5, todos dentro de UNA carpeta local (ver TENSOR_BASE_DIR
    más abajo) — vos elegís esa carpeta, solo hay que apuntarla bien acá.
  - tensor_autoencoder.py, weather_tensor_dataset.py   (en el mismo directorio
    o en el path de Python, para poder importarlos)
"""

import json
import os

import torch
from torch.utils.data import DataLoader

from tensor_autoencoder import TensorAutoencoder
from weather_tensor_dataset import WeatherTensorDataset

# ── Configuración ───────────────────────────────────────────────────────────
EMBEDDING_DIM = 32
BATCH_SIZE = 64
NUM_EPOCHS = 50
LEARNING_RATE = 1e-3
NUM_WORKERS = 2
PATIENCE = 8  # early stopping: epochs sin mejora antes de parar
CHECKPOINT_PATH = "encoder_best.pt"

# Única carpeta que hay que ajustar por entorno: dónde quedaron los .npy de
# ERA5 en ESTA máquina (Colab, VPS, laptop — lo que sea). Los JSON de refs
# solo traen el nombre de archivo, así que esta es la única variable que
# cambia entre entornos, sin parches de reemplazo de string.
# Obligatorio: fijar antes de correr este script -- no hay default valido,
# depende de donde corras esto (Colab, VPS, laptop...). Sin esto, antes se
# intentaba abrir una ruta de Colab que no existe en otros entornos.
TENSOR_BASE_DIR = None


def load_refs(path):
    with open(path) as fh:
        return json.load(fh)


def build_dataset(refs, base_dir=None, stats_path="era5_channel_stats.json"):
    if base_dir is None:
        base_dir = TENSOR_BASE_DIR  # se lee en el momento de la llamada, no al definir la función
    if base_dir is None:
        raise RuntimeError(
            "TENSOR_BASE_DIR no esta fijado. Antes de llamar a train_autoencoder.main(), "
            "asigna la carpeta real donde estan los .npy en ESTE entorno, por ejemplo:\n"
            "    import train_autoencoder as ta\n"
            "    ta.TENSOR_BASE_DIR = '/ruta/real/de/los/tensores'\n"
            "    ta.main()"
        )
    tensor_paths = [os.path.join(base_dir, r["tensor_filename"]) for r in refs]
    tensor_indices = [r["tensor_index"] for r in refs]
    return WeatherTensorDataset.from_stats_file(tensor_paths, tensor_indices, stats_path)


def run_epoch(model, loader, optimizer, device, train: bool):
    model.train(mode=train)
    total_loss = 0.0
    n_batches = 0
    criterion = torch.nn.MSELoss()

    with torch.set_grad_enabled(train):
        for batch in loader:
            batch = batch.to(device)
            recon = model(batch)
            loss = criterion(recon, batch)

            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item()
            n_batches += 1

    return total_loss / max(n_batches, 1)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_refs = load_refs("internal_train_refs.json")
    val_refs = load_refs("internal_val_refs.json")
    print(f"internal_train: {len(train_refs)} muestras | internal_val: {len(val_refs)} muestras")

    train_ds = build_dataset(train_refs)
    val_ds = build_dataset(val_refs)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                               num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda"))
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False,
                             num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda"))

    model = TensorAutoencoder(in_channels=11, embedding_dim=EMBEDDING_DIM).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    best_val_loss = float("inf")
    epochs_without_improvement = 0

    for epoch in range(1, NUM_EPOCHS + 1):
        train_loss = run_epoch(model, train_loader, optimizer, device, train=True)
        val_loss = run_epoch(model, val_loader, optimizer, device, train=False)

        print(f"Epoch {epoch:03d} | train MSE: {train_loss:.5f} | val MSE: {val_loss:.5f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            epochs_without_improvement = 0
            # Guardamos SOLO el encoder — es lo único que sobrevive a producción.
            torch.save(model.encoder.state_dict(), CHECKPOINT_PATH)
            print(f"  -> nuevo mejor val loss, encoder guardado en {CHECKPOINT_PATH}")
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= PATIENCE:
                print(f"Sin mejora en {PATIENCE} epochs, parando (early stopping).")
                break

    print(f"\nMejor val MSE: {best_val_loss:.5f}")
    print(f"Encoder congelado listo en: {CHECKPOINT_PATH}")


if __name__ == "__main__":
    main()
