"""Extraccion del bloque meteorologico ERA5 horario para la matriz maestra.

Replica la ruta `_era5_horario()` del constructor v5 (linea 2119), que es la que
alimenta de meteorologia a las variantes `sin_2020` / `sin_ntc_prev`. Se saca aqui
para poder anadirla a `dataset_horario_v04.csv`, que es del constructor v4 (23-ago) y
por tanto anterior a esa ruta: v04 tiene los 366 dias de 2020 y la semana del apagon,
pero cero columnas de meteo.

OJO CON EL NOMBRE DEL FLAG. En el constructor hay DOS caminos distintos para ERA5 y
solo uno responde a `incluir_clima`:

    _features_clima()   bloque de clima como CONTEXTO DIARIO. Lo gobierna
                        `incluir_clima`, desactivado por defecto desde el 23-ago-2026.
                        Es a esto -- y solo a esto -- a lo que se refiere el
                        "incluir_clima": false de los meta.json.

    _era5_horario()     ERA5 HORARIO. NO depende de `incluir_clima`, lo gobierna
                        `ERA5_MODO`. Es el que produce las 20 columnas `*_met_Dm1` y
                        `*_met_Dm2` que llevan las variantes v5 pese a ese `false`.

Esta es la segunda.

FRONTERA DE INFORMACION. ERA5 es REANALISIS: el tiempo que realmente ocurrio, no una
prevision. Solo es utilizable con desfase. Al predecir el precio de D+1 se decide a las
12:00 del dia D, y a esa hora la ultima jornada real cerrada es D-1 -- el dia D no entra
porque a las 12:00 solo han ocurrido sus horas 00-11 y meterlo entero seria fuga de medio
dia. De ahi los dos desfases: `met_Dm1` (dia D-1) y `met_Dm2` (dia D-2).

El constructor admite ademas `ERA5_MODO = "perfecto"`, que mete el tiempo REAL del dia
D+1. Es FUGA DELIBERADA, solo para la ablacion de cota superior ("cuanto se ganaria con
prevision meteorologica perfecta"). Aqui no se implementa: este modulo produce
exclusivamente el modo `lag`.

TRATAMIENTO POR VARIABLE, identico al del constructor. La tabla es TRIHORARIA y hay que
llevarla a horaria, pero no todas las variables se interpolan igual:

    suaves (7)   t2m, d2m, msl, wind10, wind100, wind_gust10, tcc
                 -> interpolacion temporal directa.
    radiacion    ssrd -> interpolacion y recorte a cero: la lineal genera negativos al
                 amanecer y al anochecer, que no existen fisicamente.
    lluvia       tp es un ACUMULADO, no un valor puntual: no se interpola tal cual. Se
                 convierte en ventanas moviles de 7 y 30 dias, que es la escala a la que
                 la lluvia mueve la hidraulica -- y eso no esta en ninguna prevision de
                 REE, es de las pocas cosas que ERA5 aporta de verdad.

Resultado: 10 columnas por desfase (7 suaves + 1 radiacion + 2 acumulados), 20 en total.

Uso:
    python scripts/era5_horario.py                 # escribe el parquet horario

    from era5_horario import cargar_era5, bloque_para_matriz
    era5 = cargar_era5()
    bloque = bloque_para_matriz(era5)              # unir por (fecha_objetivo, hora)
"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
from config import load_config  # noqa: E402

import psycopg2  # noqa: E402

SALIDA = REPO / "data" / "bronze" / "era5_horario.parquet"
TABLA = "era5_weather_agg"
TZ = "Europe/Madrid"

COLS_SUAVES = ["t2m_mean", "d2m_mean", "msl_mean", "wind10_mean", "wind100_mean",
               "wind_gust10_mean", "tcc_mean"]
COLS_RADIACION = ["ssrd_mean"]
COL_LLUVIA = "tp_mean"
ACUMULADOS = [7, 30]          # dias de ventana movil de precipitacion

# Desfase en dias respecto de `fecha_objetivo` (= D+1). Mismos numeros que usa
# `_desplazar_dias` en el constructor: +2 deja el dato de D-1 sobre la fila de D+1.
DESFASES = {"met_Dm1": 2, "met_Dm2": 3}


def _conectar():
    _, db = load_config()
    return psycopg2.connect(**db)


def cargar_era5(verbose: bool = True) -> pd.DataFrame:
    """ERA5 trihorario -> horario en hora de Madrid, con el tratamiento propio de cada variable."""
    con = _conectar()
    try:
        columnas = COLS_SUAVES + COLS_RADIACION + [COL_LLUVIA]
        df = pd.read_sql(
            f"SELECT ts, {', '.join(columnas)} FROM {TABLA} ORDER BY ts", con)
    finally:
        con.close()

    # La columna `ts` viene naive. Mezclar naive y aware en un join falla en silencio,
    # asi que se localiza a UTC de forma explicita antes de nada.
    df["ts"] = pd.to_datetime(df["ts"])
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize("UTC")
    df = df.set_index("ts").sort_index()

    rejilla = pd.date_range(df.index.min(), df.index.max(), freq="h", tz="UTC")
    out = pd.DataFrame(index=rejilla)

    for c in COLS_SUAVES:
        out[c] = df[c].reindex(rejilla.union(df.index)).interpolate("time").reindex(rejilla)

    for c in COLS_RADIACION:
        v = df[c].reindex(rejilla.union(df.index)).interpolate("time").reindex(rejilla)
        out[c] = v.clip(lower=0)

    tp = df[COL_LLUVIA].reindex(rejilla.union(df.index)).interpolate("time").reindex(rejilla)
    tp = tp.clip(lower=0)
    for d in ACUMULADOS:
        out[f"tp_acum_{d}d"] = tp.rolling(24 * d, min_periods=1).sum()

    loc = out.index.tz_convert(TZ)
    out = out.reset_index(drop=True)
    out["fecha"], out["hora"] = loc.date, loc.hour
    # La hora repetida del retroceso horario de octubre se colapsa promediando, que es
    # el mismo criterio del constructor.
    out = out.groupby(["fecha", "hora"], as_index=False).mean()

    medidas = [c for c in out.columns if c not in ("fecha", "hora")]
    if verbose:
        print(f"ERA5 horario: {len(out):,} horas x {len(medidas)} variables")
        print(f"  rango  : {out['fecha'].min()} -> {out['fecha'].max()}")
        print(f"  origen : {TABLA} (trihorario, {len(df):,} filas) -> interpolado a horario")
        nulos = out[medidas].isna().sum()
        nulos = nulos[nulos > 0]
        if len(nulos):
            print("  nulos tras interpolar:")
            for c, n in nulos.items():
                print(f"      {c:18s} {n:6,d} ({n / len(out) * 100:.2f}%)")
        else:
            print("  nulos tras interpolar: ninguno")
    return out


def bloque_para_matriz(era5: pd.DataFrame, desfases: dict = None) -> pd.DataFrame:
    """Bloque listo para unir por (`fecha_objetivo`, `hora`).

    Un juego de columnas por desfase, con el sufijo del constructor: `met_Dm1` es el
    dia D-1 y `met_Dm2` el D-2, ambos ya conocidos a las 12:00 de D. El emparejamiento
    es HORA A HORA: la meteorologia de la hora h del dia de referencia va a la fila cuyo
    objetivo es la hora h de D+1.
    """
    desfases = DESFASES if desfases is None else desfases
    medidas = [c for c in era5.columns if c not in ("fecha", "hora")]

    piezas = []
    for sufijo, dias in desfases.items():
        p = era5.copy()
        p["fecha_objetivo"] = pd.to_datetime(p["fecha"]) + pd.Timedelta(days=dias)
        p = p.rename(columns={c: f"{c}_{sufijo}" for c in medidas})
        piezas.append(p[["fecha_objetivo", "hora"] + [f"{c}_{sufijo}" for c in medidas]])

    bloque = piezas[0]
    for p in piezas[1:]:
        bloque = bloque.merge(p, on=["fecha_objetivo", "hora"], how="outer")
    return bloque


if __name__ == "__main__":
    era5 = cargar_era5()
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    era5.to_parquet(SALIDA, index=False)
    print(f"\nGuardado -> {SALIDA.relative_to(REPO)}")

    bloque = bloque_para_matriz(era5)
    print(f"Bloque para la matriz: {bloque.shape[0]:,} filas x {bloque.shape[1]} columnas")
    print(f"  {[c for c in bloque.columns[:5]]} ...")
