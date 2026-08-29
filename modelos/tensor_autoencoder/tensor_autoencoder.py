"""
Autoencoder CNN para embeddings de tensores meteorológicos (ERA5 / ECMWF).

Shape de entrada por muestra (un solo timestep, según decisión ya tomada de
tensor único por muestra, no secuencia): (H=33, W=57, C=11) tal como se
almacena en los .npy (channels-last). PyTorch espera channels-first
(C, H, W) — la conversión se hace en el Dataset/collate al cargar los datos,
NO en este módulo.

Diseño:
- Encoder: 3 capas Conv2d (la primera sin reducir tamaño, las siguientes dos
  con stride 2) + capa densa final a dimensión de embedding K.
- Decoder: mirror con ConvTranspose2d. Solo se usa durante el entrenamiento
  no supervisado (reconstrucción). Una vez entrenado y congelado, SOLO
  encoder.encoder se usa en producción para generar embeddings; el decoder
  se descarta.

Disciplina de fuga (ya fijada en el proyecto, no negociable):
- Normalización (media/std por canal) calculada ÚNICAMENTE con tensores de
  train (<= 2024-12-31).
- Autoencoder entrenado ÚNICAMENTE con tensores de train.
- Embeddings generados para TODO el histórico (train+val+test) una vez el
  encoder está congelado — ver política de causalidad del proyecto para el
  criterio de qué tensor usar en cada fecha D.

Verificación de shapes (con H=33, W=57, kernel=3, stride=2, padding=1):
  33 -> 17 -> 9   (conv2, conv3)
  57 -> 29 -> 15  (conv2, conv3)
  flatten: 64 * 9 * 15 = 8640
El decoder revierte exactamente estos pasos (output_padding=0 en ambas
ConvTranspose2d alcanza para recuperar 33x57 exacto — verificado a mano).
"""

import torch
import torch.nn as nn


class TensorEncoder(nn.Module):
    """Encoder convolucional. Entrada: (B, 11, 33, 57). Salida: (B, K)."""

    def __init__(self, in_channels: int = 11, embedding_dim: int = 32):
        super().__init__()
        self.embedding_dim = embedding_dim

        self.conv1 = nn.Conv2d(in_channels, 16, kernel_size=3, stride=1, padding=1)
        self.conv2 = nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1)
        self.conv3 = nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1)
        self.act = nn.ReLU(inplace=True)

        self._flat_dim = 64 * 9 * 15  # 8640, ver verificación arriba
        self.fc = nn.Linear(self._flat_dim, embedding_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.act(self.conv1(x))
        x = self.act(self.conv2(x))
        x = self.act(self.conv3(x))
        x = x.flatten(start_dim=1)
        return self.fc(x)


class TensorDecoder(nn.Module):
    """Decoder espejo, solo para entrenamiento. Reconstruye (B, 11, 33, 57)."""

    def __init__(self, out_channels: int = 11, embedding_dim: int = 32):
        super().__init__()
        self._unflat_shape = (64, 9, 15)
        flat_dim = 64 * 9 * 15

        self.fc = nn.Linear(embedding_dim, flat_dim)
        self.deconv1 = nn.ConvTranspose2d(
            64, 32, kernel_size=3, stride=2, padding=1, output_padding=0
        )  # (9,15) -> (17,29)
        self.deconv2 = nn.ConvTranspose2d(
            32, 16, kernel_size=3, stride=2, padding=1, output_padding=0
        )  # (17,29) -> (33,57)
        self.conv_out = nn.Conv2d(16, out_channels, kernel_size=3, stride=1, padding=1)
        self.act = nn.ReLU(inplace=True)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        x = self.fc(z)
        x = x.view(-1, *self._unflat_shape)
        x = self.act(self.deconv1(x))
        x = self.act(self.deconv2(x))
        # Sin activación final: las variables meteorológicas son continuas
        # y ya estarán normalizadas (media 0, std 1) antes de entrar acá.
        return self.conv_out(x)


class TensorAutoencoder(nn.Module):
    """Wrapper encoder+decoder para entrenamiento. En producción se usa solo .encoder."""

    def __init__(self, in_channels: int = 11, embedding_dim: int = 32):
        super().__init__()
        self.encoder = TensorEncoder(in_channels, embedding_dim)
        self.decoder = TensorDecoder(in_channels, embedding_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        return self.decoder(z)


if __name__ == "__main__":
    # Sanity check con datos sintéticos — no requiere acceso al VPS ni a Colab.
    batch_size = 4
    dummy = torch.randn(batch_size, 11, 33, 57)
    model = TensorAutoencoder(in_channels=11, embedding_dim=32)
    recon = model(dummy)
    print(f"Input shape:  {dummy.shape}")
    print(f"Recon shape:  {recon.shape}")
    assert recon.shape == dummy.shape, "El decoder no reconstruye la forma original"
    print("OK: shapes coinciden")