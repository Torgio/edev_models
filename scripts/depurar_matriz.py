"""Depuracion de la matriz cruda: que se tira, que es cero y que se reconstruye.

El principio, y de el sale todo lo demas: UN NULO NO ES UN CERO, y hay que saber cual es
cual antes de tocar nada. Poner 0 donde el dato falta inventa un sistema que no existio;
dejar NULL donde el 0 es el valor real tira informacion buena y obliga a imputar despues
algo que ya se sabia.

Asi que las 657.858 celdas nulas de la matriz cruda (5,9 %) se separan en cuatro casos, y
cada uno tiene su tratamiento. Ninguno es "imputar por la mediana".

1. NO SE IMPUTA, SE RETIRA
   Las 8 columnas `*_fc` son la prevision meteorologica cruda, y ya esta fundida en el
   canal `*_meteo` junto con ERA5 (ver `construir_matriz.py`). Sus 37.243 nulos por columna
   son "todavia no habia previsiones", no un hueco: la informacion esta entera en `*_meteo`
   y la bandera `meteo_es_forecast` dice de donde sale cada fila. Mantenerlas duplicaria el
   canal y arrastraria 297.944 nulos que no significan nada.

   Y 5 `capinst_*` son constantes en los seis anos y medio. Varianza cero, aportacion cero.

   Entre las dos cosas se va el 45 % de los nulos sin imputar una sola celda.

2. EL NULO ES UN CERO -- porque la fuente no escribe ceros
   ESIOS usa NULL para "no hubo". Se ve en que la columna no tiene NI UN cero explicito en
   seis anos: `bil_coal_mw` es 91 % nulos y cero ceros. Si de verdad hubiera dias con
   contrato nulo, apareceria algun 0.

       interconexion con Marruecos y Andorra   sin intercambio programado esa hora
       bilaterales de intermediacion           sin contrato

   Y las baterias, por la misma razon pero por ser una serie recien nacida: arranca el
   20-nov-2024 y publica a saltos hasta que se estabiliza en marzo de 2026. Es generacion,
   asi que su nulo es un cero, y con la potencia de baterias que habia en 2024-25 el cero
   ademas aproxima bien.

   Ojo con no pasarse: Francia y Portugal tienen huecos sueltos (menos del 0,5 %) y ahi el
   NULL SI es un fallo de publicacion, no una ausencia de flujo. Se reconstruyen.

3. EL NULO ES UN CERO -- porque la tecnologia no existia
   La capacidad instalada de baterias e hibridos aparece a mitad de la serie.
   `capinst_battery_hybrid_mw` no tiene dato hasta el 1-ene-2024, y eso no es un hueco:
   antes de esa fecha la capacidad instalada de baterias hibridas en el sistema era 0 MW.

   Por eso el tratamiento es de ESCALON, no un 0 plano: cero hasta el primer dato real, y
   a partir de ahi ya es una serie normal y sus huecos se tratan como tales.

4. EL NULO ES UN HUECO
   Y entonces se reconstruye por el eje temporal, nunca por un estadistico global.

       capacidad instalada    ffill: es un escalon administrativo. Una central no
                              desaparece un martes y vuelve el jueves; el ultimo valor
                              conocido sigue vigente hasta que cambie.
       commodities            ffill: faltan 9 dias sueltos y NINGUNO es fin de semana, o
                              sea festivos de mercado. Un gas a 0 EUR/MWh seria falso, y
                              una interpolacion inventaria una cotizacion que no existio:
                              en un dia sin mercado el precio vigente es el del cierre
                              anterior, que es literalmente lo que hace el ffill.
       lo demas               interpolacion temporal acotada a 3 horas. Un hueco de dos
                              horas en la temperatura se interpola; uno de doce, no: se
                              copia la misma hora de hace 7 dias, que conserva la forma
                              del dia en vez de trazar una recta.

5. LO QUE NO SE PUEDE COMPLETAR NO SE MAQUILLA: SE VA
   Dos casos, y ninguno es imputable porque el dato no es que falte, es que no puede
   existir.

       arranque de la serie   los lags mas largos son de 6 dias, asi que la primera semana
                              no tiene de donde salir. Se descartan 168 filas.
       hora inexistente       el domingo del cambio de hora de primavera tiene 23 horas:
                              las 2:00 no existen. La fila del dia siguiente arrastra el
                              hueco en las columnas con desfase de un dia, y en los dos
                              testigos de publicacion se resuelve por definicion -- si la
                              hora no existio, no hubo programa: 0.

RESULTADO: 0 nulos, de 657.300. Y ni una sola celda imputada por la mediana o por la media
de la columna, que es lo que se queria evitar: cada hueco se ha resuelto por lo que ese
hueco significaba.

El apagon va aparte, en `apagon.py`, y se aplica ANTES: sustituye en bloque la ventana
contaminada y rellena los huecos de la resaca. Si se hiciera despues, la interpolacion ya
habria tapado agujeros que en realidad habia que sustituir enteros.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- 1. lo que se retira ------------------------------------------------------
SUFIJOS_RETIRAR = ("_fc",)          # prevision cruda, ya fundida en `*_meteo`

# --- 2. el NULL de la fuente significa 0 --------------------------------------
# Enumeradas una a una y no por regla: la diferencia entre "no hubo intercambio" y "no se
# publico el dato" no se deduce del nombre, se mira en la serie. Criterio aplicado: mas de
# un 5 % de nulos Y ni un solo cero explicito en seis anos.
CERO_POR_CONVENCION = (
    "pbfli_net_flow_ma_mw",     # Marruecos: 54 % nulos, 0 ceros explicitos
    "pbfli_net_flow_ad_mw",     # Andorra:   12 % nulos, 0 ceros explicitos
    "bil_",                     # bilaterales de intermediacion: sin contrato
    # Baterias. Serie recien nacida (primer dato 20-nov-2024) y de publicacion
    # intermitente: 739 huecos sueltos hasta que se estabiliza el 31-mar-2026, algunos de
    # dias enteros. Es una tecnologia de generacion, asi que su nulo es un cero -- y en
    # 2024-25 la potencia de baterias del sistema es marginal, asi que el cero ademas
    # aproxima bien.
    "ree_cbattery", "ree_gbattery",
)

# --- 3. cero hasta que la tecnologia existe -----------------------------------
CERO_ANTES_DEL_PRIMER_DATO = (
    "capinst_",                 # capacidad instalada: antes de existir, 0 MW
)

# --- 4. huecos: como se reconstruye -------------------------------------------
FFILL = ("capinst_", "gas_", "co2_")
LIMITE_INTERPOLACION = 3        # horas; por encima no se interpola

# Recurso para los huecos que la interpolacion no alcanza: la misma hora de hace 7 dias.
# Quedan dos, y los dos son bloques largos de una sola columna -- 12 horas del enlace con
# Portugal el 8 de mayo y una hora del frances el 14 de diciembre --, o sea fallos de
# publicacion en una serie que el resto del ano esta completa.
#
# Interpolar 12 horas seguidas trazaria una recta entre las 11:00 y las 21:00 e inventaria
# un perfil plano donde el intercambio tiene forma de dia. El analogo de hace una semana
# conserva esa forma, y es el mismo criterio ya usado en `apagon.py`: cuando el hueco es
# largo, se copia un dia comparable en lugar de dibujar una linea.
ANALOGO_DIAS = 7

# Testigos de publicacion en la hora que el reloj se salto. `pbf_publicado_D` pregunta
# "hubo programa para esa hora", y para una hora que no existio la respuesta es no.
TESTIGOS_A_CERO = ("pbf_publicado", "pbf_completo")

# --- 5. filas que no se pueden completar y se van -----------------------------
# Los lags mas largos son de 6 dias, asi que la primera semana no tiene de donde salir. No
# es un hueco imputable: es que antes del 1-ene-2020 no hay serie. Se descarta el arranque.
ARRANQUE_DIAS = 7

# El domingo del cambio de hora de primavera tiene 23 horas: las 2:00 NO EXISTEN. La matriz
# genera la fila igual porque indexa por (fecha, hora) y sale entera a NaN. Imputarla seria
# fabricar una hora que el reloj se salto.
TZ = "Europe/Madrid"

NO_TOCAR = ("fecha_", "hora", "ts", "split", "d1_", "dias_desde_cierre",
            "imputado_apagon", "ventana_pisa_apagon", "meteo_es_forecast",
            "pbf_publicado", "pbf_completo")


# Una columna cuya serie no empieza hasta pasado este tramo de la ventana deja de ser una
# variable y pasa a ser un reloj: vale 0 durante anos y luego arranca, asi que lo que el
# modelo aprende de ella es EN QUE ANO ESTA, no el fenomeno que mide. Se marcan para que la
# seccion de matrices candidatas las saque del pool.
UMBRAL_ARRANQUE_TARDIO = 0.40


def _casa(col: str, claves) -> bool:
    return any(col.startswith(k) or col.endswith(k) for k in claves)


def perfil_series(datos: pd.DataFrame) -> pd.DataFrame:
    """Cuando arranca de verdad cada columna, y cuantos de sus ceros son fabricados.

    La distincion que importa y que no se ve mirando solo el porcentaje de ceros:

        cero REAL         el carbon no arranca esa hora, es de noche y no hay radiacion,
                          Espana y Portugal casan al mismo precio. Es el dato, y es
                          informacion de primera.
        cero FABRICADO    la serie no existia todavia. `capinst_battery_hybrid_mw` vale 0
                          cuatro anos seguidos no porque no hubiera baterias hibridas, sino
                          porque ESIOS no publicaba esa columna.

    Los dos se ven igual en la matriz depurada -- son un 0 -- y por eso hay que mirarlo
    ANTES de imputar, sobre la matriz cruda, donde el segundo caso todavia es NULL.
    """
    f = pd.to_datetime(datos["fecha_objetivo"])
    span = len(datos)
    filas = []
    for c in datos.select_dtypes("number").columns:
        if _casa(c, NO_TOCAR):
            continue
        ok = datos[c].notna()
        if not ok.any():
            continue
        ini = f[ok].min()
        antes = int((f < ini).sum())
        post = datos.loc[f >= ini, c]
        filas.append({
            "variable": c,
            "arranca": ini.date(),
            "ceros_fabricados": antes,
            "pct_sin_serie": round(antes / span * 100, 1),
            "pct_ceros_reales": round(float((post == 0).mean()) * 100, 1),
            "arranque_tardio": antes / span > UMBRAL_ARRANQUE_TARDIO,
        })
    return (pd.DataFrame(filas).sort_values("pct_sin_serie", ascending=False)
            .reset_index(drop=True))


def corte_temporal(perfil: pd.DataFrame, datos: pd.DataFrame) -> pd.DataFrame:
    """Que costaria empezar la serie mas tarde, para cada arranque candidato.

    La alternativa a podar columnas es podar fechas, y conviene verlas juntas: cada fecha
    de arranque recupera unas columnas a cambio de unas filas, y casi siempre el cambio es
    malisimo. Con esta matriz, exigir que TODAS las columnas tengan serie desde el primer
    dia deja el entrenamiento en 912 filas.
    """
    f = pd.to_datetime(datos["fecha_objetivo"])
    tr = datos["split"] == "train"
    filas = []
    for fecha in sorted(set(perfil.loc[perfil.ceros_fabricados > 0, "arranca"])):
        ts = pd.Timestamp(fecha)
        quedan = f >= ts
        filas.append({
            "arranque": fecha,
            "columnas_con_serie_completa": int((perfil.arranca <= fecha).sum()),
            "filas": int(quedan.sum()),
            "pct_filas_perdidas": round(float((~quedan).mean()) * 100, 1),
            "filas_train": int((tr & quedan).sum()),
        })
    return pd.DataFrame(filas)


def columnas_a_retirar(datos: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Devuelve `(redundantes, constantes)`."""
    red = [c for c in datos.columns if _casa(c, SUFIJOS_RETIRAR)]
    num = datos.select_dtypes("number").columns
    cte = [c for c in num if c not in red and not _casa(c, NO_TOCAR)
           and datos[c].nunique(dropna=True) <= 1]
    return red, cte


def _orden_temporal(datos: pd.DataFrame) -> np.ndarray:
    ts = pd.to_datetime(datos["fecha_objetivo"]) + pd.to_timedelta(datos["hora"], unit="h")
    return np.argsort(ts.to_numpy(), kind="stable")


def depurar(datos: pd.DataFrame, verbose: bool = True):
    """Aplica los cuatro tratamientos. Devuelve `(datos, informe)`.

    El informe lleva una fila por columna tocada, con cuantas celdas se resolvieron por
    cada via. Es lo que va al notebook y a la memoria: sin el, la matriz final no se puede
    defender.
    """
    d = datos.copy()
    red, cte = columnas_a_retirar(d)
    d = d.drop(columns=red + cte)

    # Filas que no se pueden completar: el arranque de la serie y las horas que el reloj
    # se salto. Se van antes de imputar, para no contaminar el informe con huecos que no
    # son huecos.
    fo = pd.to_datetime(d["fecha_objetivo"])
    arranque = fo < fo.min() + pd.Timedelta(days=ARRANQUE_DIAS)
    local = (fo + pd.to_timedelta(d["hora"], unit="h")).dt.tz_localize(
        TZ, ambiguous=True, nonexistent="NaT")
    inexistente = local.isna()
    d = d.loc[~(arranque | inexistente)].reset_index(drop=True)

    orden = _orden_temporal(d)
    cols = [c for c in d.select_dtypes("number").columns if not _casa(c, NO_TOCAR)]
    filas = []

    # Posicion de la fila de hace `ANALOGO_DIAS`, misma hora. Se calcula una vez.
    fo = pd.to_datetime(d["fecha_objetivo"])
    pos = pd.Series(np.arange(len(d)), index=pd.MultiIndex.from_arrays([fo, d["hora"]]))
    idx = pos.reindex(pd.MultiIndex.from_arrays(
        [fo - pd.Timedelta(days=ANALOGO_DIAS), d["hora"]])).to_numpy()
    hay_analogo = ~np.isnan(idx)
    analogo = np.where(hay_analogo, np.nan_to_num(idx, nan=0).astype(int), 0)

    for c in cols:
        s = d[c].iloc[orden]
        antes = int(s.isna().sum())
        if antes == 0:
            continue
        via = {"cero_convencion": 0, "cero_sin_serie": 0, "cero_no_existia": 0,
               "ffill": 0, "interpolado": 0, "analogo_7d": 0}

        if _casa(c, CERO_POR_CONVENCION):
            # El cero de convencion solo vale desde que la serie existe. Marruecos arranca
            # el 16-feb-2021 y antes no hay columna: rellenar ese tramo con 0 no dice "no
            # hubo intercambio", dice "no hay dato" -- y el enlace con Marruecos llevaba
            # decadas funcionando. Se rellena igual, porque la matriz sale sin nulos, pero
            # se contabiliza aparte para que `perfil_series` lo pueda senalar.
            if s.notna().any():
                previo = np.arange(len(s)) < int(np.argmax(s.notna().to_numpy()))
                via["cero_sin_serie"] = int((s.isna() & previo).sum())
            via["cero_convencion"] = int(s.isna().sum()) - via["cero_sin_serie"]
            s = s.fillna(0.0)
        else:
            if _casa(c, CERO_ANTES_DEL_PRIMER_DATO) and s.notna().any():
                previo = np.arange(len(s)) < int(np.argmax(s.notna().to_numpy()))
                n = int((s.isna() & previo).sum())
                if n:
                    via["cero_no_existia"] = n
                    s = s.mask(pd.Series(previo, index=s.index) & s.isna(), 0.0)
            if _casa(c, FFILL):
                n = int(s.isna().sum())
                s = s.ffill()
                via["ffill"] = n - int(s.isna().sum())
            else:
                n = int(s.isna().sum())
                s = s.interpolate(method="linear", limit=LIMITE_INTERPOLACION,
                                  limit_area="inside")
                via["interpolado"] = n - int(s.isna().sum())

        d[c] = s.reindex(d.index)

        # Ultimo recurso: lo que la interpolacion no alcanza, del analogo de hace 7 dias.
        if d[c].isna().any():
            origen = d[c].to_numpy()[analogo]
            aplicable = d[c].isna().to_numpy() & hay_analogo & ~np.isnan(origen)
            if aplicable.any():
                d.loc[aplicable, c] = origen[aplicable]
                via["analogo_7d"] = int(aplicable.sum())

        filas.append({"variable": c, "nulos_antes": antes, **via,
                      "sin_reconstruir": int(d[c].isna().sum())})

    # Testigos: la hora que no existio no tuvo programa.
    for c in [c for c in d.columns if c.startswith(TESTIGOS_A_CERO)]:
        n = int(d[c].isna().sum())
        if n:
            d[c] = d[c].fillna(0).astype(int)
            filas.append({"variable": c, "nulos_antes": n, "cero_hora_inexistente": n,
                          "sin_reconstruir": 0})

    inf = pd.DataFrame(filas).fillna(0)
    if len(inf):
        inf = inf.sort_values("nulos_antes", ascending=False).reset_index(drop=True)

    if verbose:
        n0, n1 = int(datos.isna().sum().sum()), int(d.isna().sum().sum())
        print(f"Retiradas {len(red)} columnas redundantes (`*_fc`, ya en `*_meteo`) "
              f"y {len(cte)} constantes")
        if cte:
            print(f"    constantes: {', '.join(cte)}")
        print(f"Descartadas {int(arranque.sum())} filas de arranque "
              f"(primeros {ARRANQUE_DIAS} dias, sin lag posible) y "
              f"{int(inexistente.sum())} horas inexistentes por el cambio de hora")
        print(f"Matriz: {datos.shape[0]:,} x {datos.shape[1]}  ->  "
              f"{d.shape[0]:,} x {d.shape[1]}")
        print(f"Nulos : {n0:,} -> {n1:,}  ({n1 / d.size * 100:.3f}% de la matriz)")
        if len(inf):
            print("\nPor via de resolucion:")
            for k, et in (("cero_convencion", "NULL=0 (convencion de la fuente)"),
                          ("cero_sin_serie", "NULL=0 (la serie no existia aun)"),
                          ("cero_no_existia", "NULL=0 (tecnologia inexistente)"),
                          ("cero_hora_inexistente", "NULL=0 (hora que el reloj se salto)"),
                          ("ffill", "ffill (escalon / cotizacion vigente)"),
                          ("interpolado", "interpolacion temporal"),
                          ("analogo_7d", "analogo de hace 7 dias (hueco largo)")):
                if k not in inf.columns:
                    continue
                sub = inf[inf[k] > 0]
                print(f"    {et:42s} {int(inf[k].sum()):9,d} celdas  "
                      f"{len(sub):3d} columnas")
            q = inf[inf.sin_reconstruir > 0]
            print(f"    {'sin reconstruir (se declara)':42s} "
                  f"{int(inf.sin_reconstruir.sum()):9,d} celdas  {len(q):3d} columnas")
    return d, inf


def residuo(datos: pd.DataFrame) -> pd.DataFrame:
    """Que dias siguen incompletos y por que. Para cerrar el capitulo en la memoria."""
    n = datos.isna().sum()
    n = n[n > 0]
    if not len(n):
        return pd.DataFrame(columns=["fecha", "columnas", "celdas"])
    m = datos[n.index].isna().any(axis=1)
    f = pd.to_datetime(datos.loc[m, "fecha_objetivo"]).dt.date
    return (pd.DataFrame({"fecha": f,
                          "celdas": datos.loc[m, n.index].isna().sum(axis=1)})
            .groupby("fecha").agg(celdas=("celdas", "sum"), horas=("celdas", "size"))
            .reset_index().sort_values("celdas", ascending=False))


if __name__ == "__main__":
    from pathlib import Path
    from apagon import imputar

    REPO = Path(__file__).resolve().parent.parent
    datos = pd.read_parquet(REPO / "data" / "bronze" / "matriz_cruda.parquet")
    datos, _ = imputar(datos)
    print()
    datos, inf = depurar(datos)

    q = residuo(datos)
    print("\nDias que quedan incompletos:", "ninguno" if not len(q) else "")
    if len(q):
        print(q.head(15).to_string(index=False))

    salida = REPO / "data" / "silver" / "matriz_depurada.parquet"
    salida.parent.mkdir(parents=True, exist_ok=True)
    datos.to_parquet(salida, index=False)
    inf.to_csv(salida.with_name("informe_depuracion.csv"), index=False)
    print(f"\nGuardado: {salida}")
    print(f"          {salida.with_name('informe_depuracion.csv')}")
    print("\nreparto:", dict(datos["split"].value_counts()))
