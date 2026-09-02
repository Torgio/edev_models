"""
Consulta ecmwf_forecast_agg (ver ecmwf_forecast_historico.py / ecmwf_tensor_historico.py)
para armar las listas de (ts, tensor_filename, tensor_index, fuente) que necesita
generate_embeddings.py para el tramo de ECMWF real.

A diferencia de query_tensor_refs.py (ERA5), este script NO separa train/val interno
para entrenar nada — el encoder nunca se entrena con ECMWF, solo se usa para INFERENCIA
(embeddings con el encoder ya congelado). Por eso solo hacen falta dos exports:

  - ecmwf_full_history_refs.json   Todo lo disponible desde INICIO_ECMWF_REAL, sea cual
                                    sea el estado actual del backfill. Para generar
                                    embeddings sobre lo que ya existe hoy.
  - ecmwf_overlap_train_refs.json  Restringido a train (<=2024-12-31), para medir el
                                    error ERA5-vs-ECMWF que alimenta el generador de
                                    pseudo-tensores — nunca tocar val/test para esto,
                                    mismo criterio que ya se aplicó a las stats de
                                    normalización.

La columna `fuente` distingue el GRIB oficial del cron (8 posiciones/dia, trihorario) del
backfill de Open-Meteo (24 posiciones/dia, horario) — no hace falta que
generate_embeddings.py distinga entre ambos (solo lee tensor_index dentro del archivo que
corresponda), pero sirve para diagnóstico de cobertura.

IMPORTANTE: correr en el VPS (necesita Postgres). ecmwf_forecast_agg la está escribiendo
un cron en este mismo momento — el resultado es una foto del momento en que se corre,
no un dato fijo.
"""

import json
import re
from collections import Counter

import psycopg2
from config import load_config

TABLA = "ecmwf_forecast_agg"

# Medido en ecmwf_forecast_historico.py: antes de esto el archivo de Open-Meteo no tiene
# cobertura completa (viento a 100m incompleto hasta entonces). No hay ECMWF real antes
# de esta fecha, sea cual sea el estado del backfill.
INICIO_ECMWF_REAL = "2024-04-01"
FIN_TRAIN = "2024-12-31 23:59:59"  # mismo corte que TRAIN_END en query_tensor_refs.py


def _safe_basename(path: str) -> str:
    """Mismo basename robusto que query_tensor_refs.py -- ver ese archivo para el porqué
    (rutas con separadores mezclados según el entorno de origen)."""
    return re.split(r"[\\/]+", path)[-1]


def get_ecmwf_refs(conn, start_ts=None, end_ts=None):
    """
    Devuelve lista de tuplas (ts, tensor_path, tensor_index, fuente), ordenadas por ts.
    Filtra tensor_path/tensor_index NULL -- días sin tensor todavía (backfill en curso).
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
        SELECT ts, tensor_path, tensor_index, fuente
        FROM {TABLA}
        WHERE {" AND ".join(where)}
        ORDER BY ts
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_full_history_ecmwf_refs(conn):
    """Todo lo disponible desde que existe ECMWF real -- para generar embeddings."""
    return get_ecmwf_refs(conn, start_ts=INICIO_ECMWF_REAL)


def get_overlap_train_refs(conn):
    """Solape ERA5-ECMWF real, restringido a train (<=2024-12-31) -- para medir el
    error de previsión que alimenta el pseudo-tensor, sin tocar val/test."""
    return get_ecmwf_refs(conn, start_ts=INICIO_ECMWF_REAL, end_ts=FIN_TRAIN)


def _export_refs(refs, path):
    serializable = [
        {
            "ts": r[0].isoformat(),
            "tensor_filename": _safe_basename(r[1]),
            "tensor_index": r[2],
            "fuente": r[3],
        }
        for r in refs
    ]
    with open(path, "w") as fh:
        json.dump(serializable, fh)
    print(f"  {path}: {len(serializable)} filas")


def _resumen(refs, etiqueta):
    print(f"\n{etiqueta}: {len(refs)} filas")
    if not refs:
        return
    por_fuente = Counter(r[3] for r in refs)
    for fuente, n in por_fuente.items():
        print(f"    fuente={fuente}: {n:,} filas")
    por_mes = Counter((r[0].year, r[0].month) for r in refs)
    print("    cobertura por mes:")
    for (y, m), n in sorted(por_mes.items()):
        print(f"      {y}-{m:02d}: {n:,} filas")


if __name__ == "__main__":
    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    full_refs = get_full_history_ecmwf_refs(conn)
    overlap_refs = get_overlap_train_refs(conn)

    _resumen(full_refs, f"ECMWF real, histórico completo (>= {INICIO_ECMWF_REAL})")
    _resumen(overlap_refs, f"ECMWF real, solape en train (>= {INICIO_ECMWF_REAL}, <= {FIN_TRAIN})")

    conn.close()

    print("\nExportando:")
    _export_refs(full_refs, "ecmwf_full_history_refs.json")
    _export_refs(overlap_refs, "ecmwf_overlap_train_refs.json")