"""
Consulta era5_weather_agg (tabla real, ver era5_load.py línea 101) para armar
las listas de (tensor_path, tensor_index) que alimentan WeatherTensorDataset:

  - get_tensor_refs(conn, start_ts=None, end_ts=None): lista genérica filtrada
    por rango de fechas.
  - Uso 1 (entrenar el autoencoder): filtrar hasta TRAIN_END_TS (<=2024-12-31),
    respetando el split cronológico del proyecto — nunca entrenar viendo
    tensores de 2025 en adelante.
  - Uso 2 (generar embeddings para producción): sin filtro de fecha, o con
    end_ts=None, para cubrir todo el histórico 2020-2026.

IMPORTANTE (ver restricción de entorno): este script necesita conectarse a
Postgres en el VPS (91.134.143.153) — correrlo ahí (o donde exista la misma
conexión), nunca desde el sandbox de Claude, que no tiene salida de red hacia
el servidor.

Ubicación sugerida: junto a era5_load.py (mismo directorio que config.py),
para que "from config import load_config" funcione sin ajustar sys.path.
"""

import json
from datetime import datetime
import os

import psycopg2
from config import load_config

DB_TABLE = "era5_weather_agg"  # debe coincidir exacto con era5_load.py línea 101
TRAIN_END_TS = "2024-12-31 23:59:59"  # último instante incluido en train

# Validación INTERNA del autoencoder (no confundir con el val=2025 real del
# proyecto, que queda reservado para la RNN/LSTM). Últimos 6 meses de train
# como tramo interno para vigilar sobreajuste sin tocar 2025 en absoluto.
INTERNAL_VAL_START = datetime(2024, 7, 1)


def get_tensor_refs(conn, start_ts: str = None, end_ts: str = None):
    """
    Devuelve lista de tuplas (ts, tensor_path, tensor_index) ordenadas por ts.
    start_ts/end_ts son strings 'YYYY-MM-DD HH:MM:SS' o None (sin límite en
    ese extremo). Filtra tensor_path/tensor_index NULL como protección extra
    (no deberían quedar tras el --force, pero mejor no asumir).
    """
    where = ["tensor_path IS NOT NULL", "tensor_index IS NOT NULL"]
    params = []
    if start_ts is not None:
        where.append("ts >= %s")
        params.append(start_ts)
    if end_ts is not None:
        where.append("ts <= %s")
        params.append(end_ts)

    sql = f"""
        SELECT ts, tensor_path, tensor_index
        FROM {DB_TABLE}
        WHERE {" AND ".join(where)}
        ORDER BY ts
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()  # [(ts, tensor_path, tensor_index), ...]


def get_train_refs(conn):
    """Solo train (<= 2024-12-31) — usar para entrenar el autoencoder."""
    return get_tensor_refs(conn, start_ts=None, end_ts=TRAIN_END_TS)


def get_full_history_refs(conn):
    """Todo el histórico disponible — usar para generar embeddings de producción."""
    return get_tensor_refs(conn, start_ts=None, end_ts=None)


def split_train_internal(train_refs, internal_val_start=INTERNAL_VAL_START):
    """
    Separa train en dos tramos INTERNOS al propio train (no toca 2025 real):
    internal_train (< internal_val_start) para entrenar, internal_val
    (>= internal_val_start, <= 2024-12-31) solo para vigilar el loss de
    reconstrucción durante el entrenamiento, sin actualizar pesos con esos
    datos. r[0] es ts como datetime.datetime (tipo nativo que devuelve
    psycopg2 para columnas TIMESTAMP).
    """
    internal_train = [r for r in train_refs if r[0] < internal_val_start]
    internal_val = [r for r in train_refs if r[0] >= internal_val_start]
    return internal_train, internal_val


def _export_refs(refs, path):
    """
    Serializa a JSON: ts -> isoformat string, tensor_path -> SOLO el nombre
    de archivo (sin el directorio del VPS), para que el JSON sea portable
    a cualquier entorno (Colab, laptop, VPS) sin parches de reemplazo de
    prefijos. Quien consuma el JSON antepone su propia ruta base local
    (ver TENSOR_BASE_DIR en train_autoencoder.py).
    """
    serializable = [
        {
            "ts": r[0].isoformat(),
            "tensor_filename": os.path.basename(r[1]),
            "tensor_index": r[2],
        }
        for r in refs
    ]
    with open(path, "w") as fh:
        json.dump(serializable, fh)
    print(f"  {path}: {len(serializable)} filas")


if __name__ == "__main__":
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    train_refs = get_train_refs(conn)
    full_refs = get_full_history_refs(conn)
    internal_train, internal_val = split_train_internal(train_refs)

    print(f"Filas de train (<= {TRAIN_END_TS}): {len(train_refs)}")
    print(f"  -> internal_train (< {INTERNAL_VAL_START.date()}): {len(internal_train)}")
    print(f"  -> internal_val   (>= {INTERNAL_VAL_START.date()}): {len(internal_val)}")
    print(f"Filas histórico completo: {len(full_refs)}")

    conn.close()

    print("\nExportando para transferir a Colab (junto con los .npy, vía Drive/SFTP):")
    _export_refs(internal_train, "internal_train_refs.json")
    _export_refs(internal_val, "internal_val_refs.json")
    _export_refs(full_refs, "full_history_refs.json")