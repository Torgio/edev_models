"""Pseudo-prevision meteorologica para el tramo sin archivo ECMWF (2020 -> marzo 2024).

EL PROBLEMA
El modelo, en produccion, recibe a las 11:00 del dia D una PREVISION del tiempo de D+1. Para
que el entrenamiento se parezca a eso, la columna meteorologica tiene que contener siempre la
misma magnitud: el tiempo de D+1.

Y no la contenia. El archivo de previsiones de Open-Meteo empieza en abril de 2024, asi que
antes de esa fecha el canal se rellenaba con ERA5 real de D-1 -- el tiempo de la vispera como
sustituto del de manana. Eso no es una version peor de la misma variable: es OTRA variable.
Medido sobre el solape, el viento a 100 m de D-1 correlaciona 0,44 con el de D+1, mientras
que la prevision de ECMWF correlaciona 0,972 con lo que luego ocurrio.

    relleno                          parecido a lo que llega en produccion
    ERA5 de D-1  (lo que habia)                    0,44
    ERA5 de D+1  (real, sin degradar)              0,97
    pseudo-prevision (esta funcion)                ~0,97 con el error correcto

POR QUE NO BASTA CON PONER EL ERA5 DE D+1 A SECAS
Por dos razones, y la segunda es la grave.

1. Error cero. El modelo aprenderia a fiarse de una columna perfecta y en produccion recibe
   una con error. Le daria mas peso del que merece.

2. SALTO DE SESGO. La prevision de Open-Meteo va sistematicamente por encima de ERA5 --
   +0,74 m/s en viento a 100 m, +0,63 en el de 10 m, es su post-proceso. Rellenar con ERA5
   crudo hace que el viento pegue un salto de +0,74 el 1 de abril de 2024, justo donde
   arranca la prevision. El modelo puede aprender ese escalon como marcador de fecha, que es
   exactamente el problema del que se venia huyendo con las columnas de arranque tardio.

QUE HACE ESTA FUNCION
Parte del ERA5 real de D+1 y lo degrada hasta que sea estadisticamente indistinguible de una
prevision:

    pseudo(D+1) = ERA5(D+1) + error remuestreado

El error NO se genera con ruido blanco. Se REMUESTREA de los errores reales medidos en el
solape (prevision - ERA5 del mismo instante, ~284 dias completos), y se hace por BLOQUES DE
24 HORAS, cogiendo el mismo dia de origen para todas las variables a la vez. Eso conserva
tres cosas que el ruido blanco destruye:

    el sesgo             viene incluido en el error, no hay que sumarlo aparte
    la autocorrelacion   un error de prevision persiste durante horas, no oscila cada hora
    la correlacion       equivocarse en la nubosidad va con equivocarse en la radiacion
                         entre variables

Con ruido blanco el modelo lo promedia a lo largo de la ventana y el error desaparece, que es
justo lo que no queremos: el objetivo es que la incertidumbre sobreviva al entrenamiento.

El bloque se toma preferentemente de un dia del MISMO MES, porque el error de prevision es
estacional -- un frente de invierno se predice peor que un anticiclon de julio.

REPRODUCIBILIDAD. Semilla fija: dos ejecuciones dan la misma matriz.

LO QUE ESTO NO ES. No es fuga en la evaluacion: validacion y test son 100 % prevision ECMWF
real y no pasan por aqui. Solo se toca el tramo de entrenamiento anterior a abril de 2024.
Aun asi la matriz marca cada fila con `meteo_es_forecast`, que ahora distingue prevision real
(1) de pseudo-prevision (0).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SEMILLA = 42

# Recortes fisicos: el error remuestreado puede sacar una variable de su rango. Una nubosidad
# de -0,03 o una radiacion negativa no existen, y un modelo que las vea aprende basura.
RECORTES = {
    "ssrd": (0.0, None),
    "tcc": (0.0, 1.0),
    "wind10": (0.0, None),
    "wind100": (0.0, None),
    "tp": (0.0, None),
}


def medir_error(fc: pd.DataFrame, era5: pd.DataFrame, variables) -> pd.DataFrame:
    """Serie horaria de errores de prevision (prevision - real) en el solape.

    `fc` y `era5` deben venir indexados igual y referidos al MISMO instante: la prevision de
    D+1 contra el ERA5 de ese mismo D+1. Comparar contra el ERA5 de otro dia mezclaria el
    error de prevision con la evolucion real del tiempo, y saldria un error inflado.
    """
    err = pd.DataFrame(index=fc.index)
    for v in variables:
        err[v] = fc[v] - era5[v]
    return err


def resumen_error(err: pd.DataFrame, era5: pd.DataFrame) -> pd.DataFrame:
    """Sesgo, RMSE y RMSE relativo por variable. Es lo que va a la memoria."""
    filas = []
    for v in err.columns:
        e = err[v].dropna()
        sd = era5[v].reindex(e.index).std()
        filas.append({"variable": v, "n": len(e), "sesgo": e.mean(),
                      "rmse": float(np.sqrt((e ** 2).mean())),
                      "rmse_rel": float(np.sqrt((e ** 2).mean()) / sd) if sd else np.nan})
    return pd.DataFrame(filas).round(3)


def _bloques_por_dia(err: pd.DataFrame, fechas: pd.Series, horas: pd.Series):
    """Reorganiza la serie de errores en bloques de 24 h: {fecha -> array (24, n_vars)}."""
    t = err.copy()
    t["_f"] = fechas.to_numpy()
    t["_h"] = horas.to_numpy()
    bloques, meses = {}, {}
    for f, g in t.groupby("_f"):
        g = g.drop_duplicates("_h").set_index("_h").reindex(range(24))
        if g[err.columns].isna().all(axis=1).any():
            continue                                    # dia incompleto, no sirve de molde
        bloques[f] = g[err.columns].to_numpy(dtype="float64")
        meses[f] = pd.Timestamp(f).month
    return bloques, meses


def pseudo_prevision(era5_objetivo: pd.DataFrame, err: pd.DataFrame,
                     fechas_err: pd.Series, horas_err: pd.Series,
                     fechas_dest: pd.Series, horas_dest: pd.Series,
                     variables, semilla: int = SEMILLA, verbose: bool = True):
    """Degrada el ERA5 del dia objetivo hasta parecer una prevision.

    Devuelve `(DataFrame con las columnas pseudo, informe)`. Cada dia de destino recibe un
    bloque de 24 h de error tomado de un dia real del solape, preferentemente del mismo mes.
    """
    rng = np.random.default_rng(semilla)
    bloques, meses = _bloques_por_dia(err[list(variables)], fechas_err, horas_err)
    if not bloques:
        raise ValueError("no hay ni un dia completo de error del que remuestrear")

    por_mes = {}
    for f, m in meses.items():
        por_mes.setdefault(m, []).append(f)
    disponibles = list(bloques)

    out = pd.DataFrame(index=era5_objetivo.index, columns=list(variables), dtype="float64")
    dias_dest = pd.Index(pd.unique(fechas_dest))
    pos = {(f, h): i for i, (f, h) in enumerate(zip(fechas_dest, horas_dest))}

    usados_mismo_mes = 0
    for f in dias_dest:
        mes = pd.Timestamp(f).month
        candidatos = por_mes.get(mes) or disponibles
        usados_mismo_mes += int(bool(por_mes.get(mes)))
        molde = bloques[candidatos[rng.integers(len(candidatos))]]
        for h in range(24):
            i = pos.get((f, h))
            if i is None:
                continue                                # hora inexistente (cambio de hora)
            out.iloc[i] = era5_objetivo.iloc[i][list(variables)].to_numpy() + molde[h]

    for v in variables:
        lo, hi = RECORTES.get(v, (None, None))
        if lo is not None or hi is not None:
            out[v] = out[v].clip(lower=lo, upper=hi)

    informe = {
        "dias_destino": len(dias_dest),
        "dias_molde_disponibles": len(bloques),
        "meses_cubiertos": len(por_mes),
        "dias_con_molde_del_mismo_mes": usados_mismo_mes,
        "semilla": semilla,
    }
    if verbose:
        print(f"  pseudo-prevision: {informe['dias_destino']:,} dias rellenados "
              f"desde {informe['dias_molde_disponibles']} dias de molde "
              f"({informe['meses_cubiertos']}/12 meses)")
    return out, informe
