"""Bloque de PREVISION meteorologica ECMWF para la matriz maestra.

Tercera pieza, junto a `pbf_horario.py` y `era5_horario.py`. Lee la prevision a D+1 de
`ecmwf_forecast_agg` y la deja lista para unir por (`fecha_objetivo`, `hora`).

QUE LO DISTINGUE DEL BLOQUE ERA5
Los dos traen las mismas magnitudes fisicas, pero no son lo mismo:

    era5_horario.py    tiempo REAL OBSERVADO de D-1 y D-2 (reanalisis). Sufijo `_met_Dm1`.
                       Problema: ERA5 se publica con ~5 dias de retraso, asi que a las
                       12:00 del dia D el dato de ayer NO existe todavia. Se entrena con
                       informacion que en produccion no se tendra.

    ecmwf_horario.py   PREVISION emitida para D+1 y disponible antes del cierre del
                       mercado. Sufijo `_fc`. Es lo que de verdad habra al predecir.

Por eso el bloque de prevision es el candidato natural a sustituir al de ERA5. Se dejan
los dos en la matriz a proposito: el analisis de redundancia y el ranking del notebook
diran cual aporta, en vez de decidirlo por decreto.

LA INTERPOLACION, Y POR QUE SE APLICA AQUI
El historico de Open-Meteo es horario, pero produccion no lo sera: el cron se alimenta de
ECMWF Open Data, que solo publica cada 3 horas. Entrenar con 24 valores medidos y servir
con 8 medidos mas 16 interpolados es un desajuste entrenamiento/produccion.

`meteo_horario.simular_produccion()` degrada la serie a las horas 0, 3, 6... y reconstruye
el resto con la misma receta que usara produccion. Cuesta poco -- medido sobre 21.128
horas: 0,03 % en temperatura, 0,8 % en viento, 2,8 % en radiacion (cubica con recorte a
cero; con lineal seria 9,3 %) -- y a cambio el modelo ve en entrenamiento el mismo tipo de
dato que vera en produccion.

Se hace en el bloque y no en la tabla a proposito: la tabla guarda lo que las fuentes
publicaron, y la reconstruccion es una decision de modelado que puede cambiar.

Uso:
    from ecmwf_horario import cargar, bloque_para_matriz
    fc = cargar()                       # desde Postgres, con la reconstruccion aplicada
    bloque = bloque_para_matriz(fc)     # unir por (fecha_objetivo, hora)
"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))
sys.path.append(str(REPO / "ingesta"))

from meteo_horario import HORAS_PRODUCCION, informe_error, simular_produccion  # noqa: E402

TABLA = "ecmwf_forecast_agg"
SUFIJO = "_fc"

# `wind_gust10_mean` no entra: no esta archivada en el historico de prevision y, agregada,
# es un 94 % viento medio reescalado (R2 = 0,943). Ver seccion 5.4 del notebook 04.
COLUMNAS = ["t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean",
            "ssrd_mean", "tcc_mean", "tp_mean", "msl_mean"]


def _conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def cargar(desde: str = "2024-04-01", reconstruir: bool = True,
           verbose: bool = True) -> pd.DataFrame:
    """Prevision horaria desde Postgres, con la reconstruccion de produccion aplicada.

    IMPORTANTE: lee la tabla ENTERA, sin filtrar por `fuente`. Desde el 8-ago-2026 las
    horas ancla (0, 3, 6...) las escribe el cron y el resto vienen del backfill; filtrar
    por una sola fuente deja esos dias sin anclas y la reconstruccion los vacia entera.
    """
    con = _conexion()
    try:
        df = pd.read_sql(
            f"SELECT ts, {', '.join(COLUMNAS)} FROM {TABLA} "
            f"WHERE ts >= %(desde)s ORDER BY ts", con, params={"desde": desde})
    finally:
        con.close()

    df["ts"] = pd.to_datetime(df["ts"])
    if verbose:
        print(f"Previsión ECMWF: {len(df):,} horas · {df['ts'].min()} -> {df['ts'].max()}")
        print(f"  nulos de origen: {int(df[COLUMNAS].isna().sum().sum())}")

    if not reconstruir:
        return df

    rec = simular_produccion(df, ts_col="ts", columnas=COLUMNAS)
    if verbose:
        anclas = df["ts"].dt.hour.isin(HORAS_PRODUCCION).sum()
        print(f"  reconstruida desde {len(HORAS_PRODUCCION)} anclas/día ({anclas:,} horas); "
              f"nulos tras reconstruir: {int(rec[COLUMNAS].isna().sum().sum())}")
        print(informe_error(df, rec, columnas=COLUMNAS)
              [["variable", "metodo", "MAE", "MAE_pct"]].round(4).to_string(index=False))
    return rec


def bloque_para_matriz(fc: pd.DataFrame) -> pd.DataFrame:
    """Listo para unir por (`fecha_objetivo`, `hora`).

    El `ts` de la tabla ES la hora valida de la prevision, es decir la hora de D+1 que se
    quiere predecir. Asi que casa directamente con la fila objetivo -- sin desplazar, a
    diferencia del PBF (que va alineado al dia D) y de ERA5 (que va a D-1 y D-2).
    """
    out = fc.copy()
    ts = pd.to_datetime(out["ts"])
    out["fecha_objetivo"] = ts.dt.normalize()
    out["hora"] = ts.dt.hour
    # El retroceso horario de octubre repite la hora local 2: la matriz indexa por
    # (fecha, hora), asi que se colapsa quedandose con la primera.
    out = (out.sort_values("ts")
              .drop_duplicates(subset=["fecha_objetivo", "hora"], keep="first"))
    ren = {c: f"{c.replace('_mean', '')}{SUFIJO}" for c in COLUMNAS}
    return out.rename(columns=ren)[["fecha_objetivo", "hora"] + list(ren.values())]


if __name__ == "__main__":
    fc = cargar()
    bloque = bloque_para_matriz(fc)
    print(f"\nBloque: {bloque.shape[0]:,} filas x {bloque.shape[1]} columnas")
    print(f"  {list(bloque.columns)}")
