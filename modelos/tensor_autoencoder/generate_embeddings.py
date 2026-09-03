"""
Genera embeddings para TODO el histórico (2020-2026, incluyendo train, val y
test) usando el encoder CNN ya entrenado y congelado. A diferencia de
train_autoencoder.py, esto es pura inferencia: sin backward, sin optimizer,
sin decoder — mucho más liviano que entrenar, y probablemente corre bien
incluso en CPU (VPS), aunque por ahora se prueba en Colab.

Requiere en el mismo directorio (o en el sys.path):
  - full_history_refs.json  (de query_tensor_refs.py — TODO el histórico,
    no solo train)
  - era5_channel_stats.json (de compute_train_stats.py — las MISMAS stats
    usadas para entrenar; nunca recalcular con el histórico completo, o el
    encoder vería una distribución distinta a la que aprendió)
  - encoder_best.pt         (pesos del encoder, ya entrenado y congelado)
  - tensor_autoencoder.py, weather_tensor_dataset.py (para importar)

Salida: embeddings_full_history.csv y embeddings_full_history.parquet, con
columnas ts, emb_0 ... emb_{K-1} — mismo patrón csv+parquet que usa el
equipo para matriz_nucleo y el resto de las matrices en data/gold/.
"""

import json
import os

import torch
from torch.utils.data import DataLoader

from tensor_autoencoder import TensorEncoder
from weather_tensor_dataset import WeatherTensorDataset

EMBEDDING_DIM = 32
BATCH_SIZE = 256  # inferencia sin gradientes: se banca batches mas grandes que en training
NUM_WORKERS = 2
CHECKPOINT_PATH = "encoder_best.pt"
REFS_PATH = "full_history_refs.json"
OUTPUT_CSV = "embeddings_full_history.csv"
OUTPUT_PARQUET = "embeddings_full_history.parquet"

# Obligatorio: fijar antes de llamar a main() -- no hay default valido, depende
# de donde corras esto (VPS, Colab, laptop...). Sin esto, antes se intentaba
# abrir una ruta de Colab que no existe en otros entornos, con un error
# confuso enterrado en el DataLoader -- ahora falla explicito, arriba.
TENSOR_BASE_DIR = None


def load_refs(path):
    with open(path) as fh:
        return json.load(fh)


def build_dataset(refs, base_dir=None, stats_path="era5_channel_stats.json"):
    if base_dir is None:
        base_dir = TENSOR_BASE_DIR
    if base_dir is None:
        raise RuntimeError(
            "TENSOR_BASE_DIR no esta fijado. Antes de llamar a generate_embeddings.main(), "
            "asigna la carpeta real donde estan los .npy en ESTE entorno, por ejemplo:\n"
            "    import generate_embeddings as ge\n"
            "    ge.TENSOR_BASE_DIR = '/ruta/real/de/los/tensores'\n"
            "    ge.main()"
        )
    tensor_paths = [os.path.join(base_dir, r["tensor_filename"]) for r in refs]
    tensor_indices = [r["tensor_index"] for r in refs]
    return WeatherTensorDataset.from_stats_file(tensor_paths, tensor_indices, stats_path)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    refs = load_refs(REFS_PATH)
    print(f"Generando embeddings para {len(refs)} timesteps")

    dataset = build_dataset(refs)
    # shuffle=False es CRITICO: preserva el orden de refs para poder
    # reasociar cada embedding con su ts correcto despues.
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False,
                         num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda"))

    encoder = TensorEncoder(in_channels=11, embedding_dim=EMBEDDING_DIM).to(device)
    encoder.load_state_dict(torch.load(CHECKPOINT_PATH, map_location=device))
    encoder.eval()  # sin dropout/batchnorm en esta arquitectura, pero buena practica igual

    all_embeddings = []
    with torch.no_grad():  # sin grafo de autograd: mas rapido y liviano que entrenar
        for batch in loader:
            batch = batch.to(device)
            emb = encoder(batch)  # (B, K)
            all_embeddings.append(emb.cpu())

    embeddings = torch.cat(all_embeddings, dim=0)  # (N, K)
    assert embeddings.shape[0] == len(refs), (
        f"Descuadre: {embeddings.shape[0]} embeddings vs {len(refs)} refs -- "
        "revisar que shuffle=False y que el Dataset no haya saltado ninguna muestra"
    )
    print(f"Embeddings generados: shape {tuple(embeddings.shape)}")

    import pandas as pd

    out_df = pd.DataFrame(
        embeddings.numpy(), columns=[f"emb_{i}" for i in range(EMBEDDING_DIM)]
    )
    out_df.insert(0, "ts", [r["ts"] for r in refs])

    out_df.to_csv(OUTPUT_CSV, index=False)
    out_df.to_parquet(OUTPUT_PARQUET, index=False)

    print(f"Guardado en {OUTPUT_CSV} y {OUTPUT_PARQUET}")


if __name__ == "__main__":
    main()
