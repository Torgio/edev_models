"""Imputacion de la ventana del apagon iberico por COPIA EN BLOQUE de la semana anterior.

QUE SE IMPUTA
Dentro de la ventana se sustituye el bloque ENTERO de las columnas de estado del sistema,
haya dato o no. El 29 y el 30 de abril falta; la tarde del 28 y el 1 de mayo existen pero
estan deprimidos -- nuclear al 0,45 de lo normal, bombeo al 0,23 -- porque son un sistema
cayendose y reponiendose. Para una feature eso es una observacion de OTRO proceso, no un
extremo que el modelo deba aprender.

Fuera de la ventana NO se toca nada, y eso incluye la semana siguiente: esta medido que
del 2 de mayo en adelante los valores caen dentro de la variabilidad normal (ver la nota
de VENTANA) y ninguna tecnologia queda a cero.

Y NO se toca el precio. `target_price`, `es_esios_D` y `gas_mibgas` tienen cero nulos esos
dias: son datos reales y publicados, anomalos pero ciertos. Sustituirlos convertiria dias
de validacion en datos inventados -- el analogo del 29-abr daria 61 EUR/MWh frente a los
5,79 que costo. Si algun dia hace falta ese contrafactual, en columna aparte y declarada.

EL METODO: copia en bloque, sin reescalar y sin ruido
Se copia la misma hora del mismo dia de la semana anterior, TODAS las columnas a la vez y
del mismo instante de origen. El desfase de 7 dias conserva el dia de la semana.

Se probaron tres variantes contra la verdad, tapando periodos completos y comparando:

    metodo                   MAE relativo    coherencia (total_gen vs suma de tecnologias)
    reescalado por referencia    85,8 %      1.580  <- TRIPLICA la incoherencia
    copia en bloque              87,4 %        565  <- igual que el nivel natural (508)
    copia + ruido 5%             88,7 %          -

El reescalado gana metro y medio de MAE por columna y a cambio rompe la fisica: cada
columna recibe un factor distinto, la identidad "total = suma de las partes" deja de
cumplirse y el estado del sistema pasa a ser imposible. Para un modelo que consume el
vector entero -- y mas para una CNN sobre el tensor -- la coherencia conjunta vale mas que
un punto de MAE por variable.

El RUIDO empeora y no aporta: anade varianza sin informacion. Si lo que se busca es que el
modelo pueda distinguir lo imputado, para eso esta la bandera `imputado_apagon`, que es
mas honesto que disimularlo.

PENSANDO EN LOS TENSORES. La copia en bloque es ademas lo unico que se puede extender al
campo espacial: un tensor `(24, 33, 57, 11)` no se puede reescalar variable a variable sin
romper la estructura espacial, pero si se puede sustituir por el del dia analogo. Mismo
criterio, misma ventana, mismo desfase.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# VENTANA, con precision de HORA. El corte fue a las 12:33 del 28-abr, asi que la manana
# de ese dia es normal y la tarde no. Termina el 1-may: a partir del 2 el sistema esta
# recuperado.
#
# Medido, dia contra su homologo de dos semanas antes (ratio, 1.00 = normal):
#
#     dia        eolica  solar  nuclear  bombeo
#     28-abr      0,85   0,92    0,45     0,23   <- deprimido
#     29-abr       ---    ---     ---    -0,00   <- ausente
#     30-abr      0,78   0,77    0,58     0,67   <- deprimido
#      1-may      0,65   0,81    1,86     0,59   <- reposicion
#      2-may      0,97   0,91    1,78     0,91
#      5-may      2,07   0,81    0,44     4,13
#     10-may      0,49   0,76    2,91     0,74   <- dia NORMAL, sin apagon
#
# Los dos ultimos son la clave: en dias normales el ratio va de 0,49 a 2,91, asi que la
# variabilidad natural contra una referencia quincenal es enorme. Del 2 de mayo en adelante
# los valores caen dentro de ese rango -- no hay forma de decir que esten afectados -- y
# ninguna tecnologia queda a cero. Extender la ventana a la semana siguiente sustituiria
# variabilidad normal por copias, que es empeorar el dato.
VENTANA = (pd.Timestamp("2025-04-28 12:00"), pd.Timestamp("2025-05-01 23:00"))
DIAS_ANALOGO = 7

# RESACA. Del 2 al 5 de mayo el sistema ya esta recuperado -- por eso la ventana de
# sustitucion cierra el dia 1 -- pero quedan columnas SIN PUBLICAR. La nuclear es el caso
# claro: 84 horas a NULL, y no es una parada, porque en seis anos su minimo son 50 MW y no
# hay ni una hora a cero. Es que REE no publico el programa.
#
# Aqui NO se sustituye: solo se rellena el hueco. El dato publicado esos dias es bueno y se
# respeta; unicamente se reconstruye lo que falta, y del mismo analogo de D-7. Es la
# distincion que importa -- dentro de VENTANA el dato existente esta contaminado y sobra,
# en RESACA el dato existente es valido y lo unico que pasa es que hay agujeros.
#
# Llega hasta el 7 porque el lag mas largo de la matriz es de 6 dias: la fila del 6 de mayo
# arrastra 231 celdas vacias en columnas `_Dm6` que apuntan al 30 de abril.
RESACA = (pd.Timestamp("2025-05-02 00:00"), pd.Timestamp("2025-05-07 23:00"))

# LO QUE NO SE TOCA, Y POR QUE.
# Del 2 al 7 de mayo las columnas con lag apuntan a dias del apagon, asi que llevan valores
# anomalos. Pero no estan CORRUPTOS: son ciertos. El sistema de verdad tuvo un cero seis
# dias antes, y una fila del 6 de mayo que dice "hace 6 dias la demanda se desplomo", con un
# precio real y normal, es un ejemplo legitimo y raro, no un error de dato. Sustituirlos
# seria reescribir historia publicada, y es justo lo contrario de la regla: en la semana
# posterior solo se toca lo que evidentemente falta.
#
# Queda una incoherencia asumida: la fila del 30 de abril ahora dice "dia normal" (copiada
# del 23) mientras la del 6 de mayo sigue diciendo "el 30 fue anomalo". Con 6 filas sobre
# 2.404 dias no compensa reescribir dato bueno, asi que se marcan con
# `ventana_pisa_apagon` y quien modele decide si las saca.
LAG_MAXIMO = 6

# QUE COLUMNAS. TODAS las medidas, precio incluido.
#
# Sustituir el estado del sistema y dejar el precio real seria lo peor de los dos mundos:
# el modelo veria condiciones normales asociadas a 5,79 EUR/MWh y aprenderia una relacion
# falsa. O se sustituye el bloque entero o no se sustituye nada.
#
# La UNICA excepcion es el calendario y las claves de fecha. No son medidas del sistema,
# son hechos del dia: el 1 de mayo es festivo y el 24 de abril no. Copiarlos pondria
# "jueves laborable" sobre un festivo y el modelo aprenderia el calendario al reves.
CLAVES_Y_CALENDARIO = ("fecha_", "hora", "ts", "split", "d1_", "dias_desde_cierre",
                       "imputado_apagon", "dia_apagon", "ventana_pisa_apagon",
                       "meteo_es_forecast", "pbf_publicado", "pbf_completo")

def _es_calendario(col: str) -> bool:
    return col.startswith(CLAVES_Y_CALENDARIO)


def columnas_afectadas(datos: pd.DataFrame, ventana=VENTANA) -> list[str]:
    """Todas las columnas numericas salvo claves y calendario."""
    num = datos.select_dtypes(include="number").columns
    return [c for c in num if not _es_calendario(c)]


def _instante(datos: pd.DataFrame) -> pd.Series:
    """Marca temporal con hora, para poder acotar el 28-abr desde las 12:00."""
    return (pd.to_datetime(datos["fecha_objetivo"])
            + pd.to_timedelta(datos["hora"], unit="h"))


def imputar(datos: pd.DataFrame, columnas=None, ventana=VENTANA, resaca=RESACA,
            dias=DIAS_ANALOGO, verbose=True):
    """Copia en bloque desde la semana anterior. Devuelve `(datos, informe)`.

    Dos pasadas, y en ese orden:

        1. VENTANA -- se sustituye TODO, hubiera dato o no. El dato de esos dias existe a
           ratos pero mide un sistema cayendose, asi que sobra.
        2. RESACA  -- se rellena SOLO lo que falta. El dato de esos dias es bueno.

    El orden importa: el analogo del 5 de mayo es el 28 de abril, que la primera pasada ya
    ha dejado limpio. Si se hiciera al reves, la resaca copiaria las horas contaminadas del
    28 por la tarde.

    En ambas, todas las columnas se toman del MISMO instante de origen: (fecha - `dias`,
    misma hora). Eso es lo que preserva la coherencia entre variables.
    """
    d = datos.copy()
    fo = pd.to_datetime(d["fecha_objetivo"])
    ts = _instante(d)
    en_ventana = (ts >= ventana[0]) & (ts <= ventana[1])
    en_resaca = (ts >= resaca[0]) & (ts <= resaca[1]) if resaca else pd.Series(False, index=d.index)
    cols = columnas if columnas is not None else columnas_afectadas(d, ventana)

    # Posicion de la fila analoga: misma hora, `dias` dias antes.
    pos = pd.Series(np.arange(len(d)),
                    index=pd.MultiIndex.from_arrays([fo, d["hora"]]))
    idx = pos.reindex(pd.MultiIndex.from_arrays(
        [fo - pd.Timedelta(days=dias), d["hora"]])).to_numpy()
    hay_analogo = ~np.isnan(idx)
    filas = np.where(hay_analogo, np.nan_to_num(idx, nan=0).astype(int), 0)

    sustituidas = {c: 0 for c in cols}
    rellenadas = {c: 0 for c in cols}
    tocadas = np.zeros(len(d), dtype=int)

    for etapa, destino in (("sustituir", sustituidas), ("rellenar", rellenadas)):
        for c in cols:
            base = en_ventana if etapa == "sustituir" else (en_resaca & d[c].isna())
            hueco = base & hay_analogo
            if not hueco.any():
                continue
            origen = d[c].to_numpy()[filas]          # releido: la pasada 1 ya limpio abril
            aplicable = hueco & ~np.isnan(origen)
            if not aplicable.any():
                continue
            d.loc[aplicable, c] = origen[aplicable.to_numpy()]
            destino[c] = int(aplicable.sum())
            tocadas += aplicable.to_numpy().astype(int)

    d["imputado_apagon"] = tocadas
    # Filas cuyas columnas con lag alcanzan la ventana. Su dato es real y se respeta; la
    # bandera existe para que se puedan excluir sin tener que redescubrir cuales son.
    d["ventana_pisa_apagon"] = (
        (ts >= ventana[0]) & (ts <= ventana[1] + pd.Timedelta(days=LAG_MAXIMO))
    ).astype(int)
    afectada = en_ventana | en_resaca
    inf = pd.DataFrame([{
        "variable": c,
        "vacias_antes": int((afectada & datos[c].isna()).sum()),
        "sustituidas": sustituidas[c],
        "rellenadas": rellenadas[c],
        "sin_reconstruir": int((afectada & d[c].isna()).sum()),
    } for c in cols if sustituidas[c] or rellenadas[c]])
    if len(inf):
        inf = inf.sort_values("sustituidas", ascending=False).reset_index(drop=True)

    if verbose and len(inf):
        print(f"Ventana {ventana[0].date()} -> {ventana[1].date()} · "
              f"{int(en_ventana.sum())} filas · sustitucion en bloque de hace {dias} dias")
        print(f"  columnas tocadas : {len(inf)}")
        print(f"  celdas sustituidas: {int(inf['sustituidas'].sum()):,}")
        if resaca is not None:
            print(f"Resaca  {resaca[0].date()} -> {resaca[1].date()} · "
                  f"{int(en_resaca.sum())} filas · solo se rellenan huecos")
            rel = inf[inf.rellenadas > 0]
            print(f"  columnas con hueco: {len(rel)}  ->  "
                  f"{', '.join(rel.variable.head(4))}" if len(rel) else "  sin huecos")
            print(f"  celdas rellenadas : {int(inf['rellenadas'].sum()):,}")
        print(f"  sin reconstruir   : {int(inf['sin_reconstruir'].sum()):,}")
    return d, inf
