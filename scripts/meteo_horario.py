"""Reconstruccion horaria de la meteorologia a partir de pasos trihorarios.

POR QUE EXISTE
El target es horario, pero la meteorologia que habra en PRODUCCION no lo es: el cron se
alimenta de ECMWF Open Data, que solo publica pasos cada 3 horas (0h, 3h, 6h... nunca
horario -- verificado contra el servidor el 28-ago-2026: los steps 25, 26, 31, 46 y 47
dan 404). ERA5 es horario de origen pero el equipo lo bajo trihorario justamente para
alinearlo con eso.

El backfill historico de prevision (Open-Meteo) SI es horario, y ahi esta la trampa: si
se entrena con 24 valores reales por dia y en produccion solo hay 8 mas interpolacion,
el modelo aprende con un dato mejor del que tendra el dia de la verdad. Es el mismo tipo
de desajuste entrenamiento/produccion que la fuga, aunque no lo parezca.

La solucion es reproducir en entrenamiento lo que hara produccion:
    `simular_produccion()`  toma la serie horaria real, se queda con las horas 0,3,6...
                            y reconstruye las otras 16. Eso es lo que debe entrar en la
                            matriz.
    `a_horario()`           hace lo mismo partiendo de una serie que YA es trihorario.
                            Es la que usara produccion.
Las dos comparten receta, que es justo el punto: el mismo codigo en los dos lados.

LA RECETA, Y POR QUE
Medido sobre las 20.952 horas reales del historico de prevision, comparando la
reconstruccion contra la verdad:

    variable      MAE      MAE %   metodo
    t2m         0.1132     0.04%   lineal
    d2m         0.0554     0.02%   lineal
    msl         9.0356     0.01%   lineal
    wind100     0.0578     0.96%   lineal
    wind10      0.0493     1.11%   lineal
    tcc         0.0057     1.62%   lineal
    tp          0.0108    14.36%   lineal   (sobre 0,075 mm de media: irrelevante)
    ssrd       19.6680     9.33%   LINEAL -> insuficiente

La radiacion es la excepcion. Una recta entre dos puntos separados 3 horas no sigue la
curvatura del ciclo diario, y el error se concentra donde mas importa: 67,8 W/m2 a las
13h, 65,1 a las 14h, 56,1 a las 11h. Con interpolacion CUBICA baja a 6,88 (3,27%), pero
genera 3.463 valores negativos al amanecer y al anochecer, que fisicamente no existen.
Recortando a cero -- la misma correccion que ya aplica `_era5_horario()` del constructor:

    ssrd    cubica + clip(0)    5.0060     2.38%

De 9,33 % a 2,38 %. Para el resto, la lineal ya da entre 0,01 % y 1,6 % y no merece
complicarlo. El dato tranquilizador: **el viento se reconstruye con ~1 % de error**, y es
la variable que manda en el precio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Horas que publica ECMWF Open Data, y por tanto las unicas que tendra produccion.
HORAS_PRODUCCION = (0, 3, 6, 9, 12, 15, 18, 21)

# Variables que necesitan cubica en vez de lineal. Se casan por subcadena para que
# valgan tanto `ssrd_mean` como `ssrd_mean_met_Dm1` o `pbf_ssrd_...`.
CUBICAS = ("ssrd",)

# Recortes fisicos tras interpolar. La cubica sobrepasa en los extremos y hay magnitudes
# que no admiten ciertos valores: la radiacion no es negativa, la nubosidad vive en [0,1],
# la lluvia acumulada no es negativa.
RECORTES = {
    "ssrd": (0.0, None),
    "tcc": (0.0, 1.0),
    "tp": (0.0, None),
}


def _recorte(col: str):
    for clave, (lo, hi) in RECORTES.items():
        if clave in col:
            return lo, hi
    return None, None


def _es_cubica(col: str) -> bool:
    return any(c in col for c in CUBICAS)


def a_horario(df: pd.DataFrame, ts_col: str = "ts", columnas=None,
              horas_disponibles=HORAS_PRODUCCION) -> pd.DataFrame:
    """Rellena a horario una serie que solo tiene las horas de `horas_disponibles`.

    Es la funcion de PRODUCCION: entra lo que publica el cron, sale la rejilla horaria
    que espera la matriz. No inventa fuera del rango observado (`limit_area="inside"`),
    asi que los bordes se quedan como esten.
    """
    out = df.copy().sort_values(ts_col).reset_index(drop=True)
    ts = pd.to_datetime(out[ts_col])
    cols = columnas or [c for c in out.columns
                        if c != ts_col and pd.api.types.is_numeric_dtype(out[c])]

    aux = out.set_index(pd.DatetimeIndex(ts))[cols]
    # Solo las horas que produccion puede dar sirven de ancla; el resto se reconstruye.
    ancla = aux.where(pd.Series(aux.index.hour, index=aux.index).isin(horas_disponibles))

    for c in cols:
        metodo = "cubic" if _es_cubica(c) else "time"
        try:
            serie = ancla[c].interpolate(method=metodo, limit_area="inside")
        except Exception:
            # La cubica necesita al menos cuatro anclas; con series cortas cae a lineal.
            serie = ancla[c].interpolate(method="time", limit_area="inside")
        lo, hi = _recorte(c)
        if lo is not None or hi is not None:
            serie = serie.clip(lower=lo, upper=hi)
        out[c] = serie.to_numpy()
    return out


def simular_produccion(df: pd.DataFrame, ts_col: str = "ts", columnas=None,
                       horas_disponibles=HORAS_PRODUCCION) -> pd.DataFrame:
    """Degrada una serie horaria REAL a lo que produccion podria ver, y la reconstruye.

    Se usa al construir la matriz de entrenamiento sobre el historico de Open-Meteo, que
    viene horario. Sin esto, el modelo entrenaria con 24 valores medidos por dia y
    serviria con 8 medidos y 16 interpolados.
    """
    return a_horario(df, ts_col=ts_col, columnas=columnas,
                     horas_disponibles=horas_disponibles)


def informe_error(df_real: pd.DataFrame, df_reconstruido: pd.DataFrame,
                  ts_col: str = "ts", columnas=None) -> pd.DataFrame:
    """Cuanto cuesta la reconstruccion, variable a variable.

    Solo se puede calcular donde se tiene la verdad horaria -- es decir, sobre el
    historico de Open-Meteo. Es la evidencia que justifica la receta en la memoria.
    """
    cols = columnas or [c for c in df_real.columns
                        if c != ts_col and pd.api.types.is_numeric_dtype(df_real[c])]
    a = df_real.set_index(pd.DatetimeIndex(pd.to_datetime(df_real[ts_col])))[cols]
    b = df_reconstruido.set_index(pd.DatetimeIndex(pd.to_datetime(df_reconstruido[ts_col])))[cols]

    filas = []
    for c in cols:
        j = pd.concat([a[c].rename("real"), b[c].rename("rec")], axis=1).dropna()
        if j.empty:
            continue
        err = (j["rec"] - j["real"]).abs()
        media = j["real"].mean()
        filas.append({
            "variable": c,
            "metodo": "cubica+clip" if _es_cubica(c) else "lineal",
            "media_real": media,
            "MAE": err.mean(),
            "MAE_pct": err.mean() / abs(media) * 100 if media else np.nan,
            "p95_error": err.quantile(0.95),
            "r": j["real"].corr(j["rec"]),
            "n": len(j),
        })
    return pd.DataFrame(filas).sort_values("MAE_pct", ascending=False).reset_index(drop=True)
