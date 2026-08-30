"""La matriz del dia, para predecir. Hermana de `matriz_nucleo`, que NO se toca.

POR QUE DOS MATRICES
`matriz_nucleo` esta congelada: el constructor del equipo lleva escrito a mano

    MODELO_END = pd.Timestamp("2026-07-31").date()   # ultimo dia predicho

y sobre ese corte estan medidos todos los numeros de la memoria -- el 11,861 del ensemble,
los MAE por familia, el grafico. Moverlo cambiaria todos a la vez. Asi que la de evaluacion
se queda quieta y produccion se construye aparte, con la misma tuberia y el corte en manana.

POR QUE LOS MODELOS SIGUEN VALIENDO SIN REENTRENAR
El reparto va por FECHA FIJA -- train hasta 2024-12-31, validacion hasta 2025-12-31 --, no
por proporcion. Anadir dias por la cola no mueve ni un dia de train, asi que los escaladores
(que se ajustan solo sobre train) salen identicos y el modelo ve la entrada en la escala que
aprendio. Lo unico que cambia es el hash, y por eso `predecir.py` avisa del hash pero solo
falla si cambian las columnas o los escaladores.

LAS COLUMNAS SE COPIAN DE NUCLEO, NO SE VUELVEN A DECIDIR
La depuracion toma decisiones que dependen de los datos: que columna se retira por arranque
tardio, donde se corta la serie. Con un mes mas de datos esas decisiones pueden salir
distintas, y una sola columna de mas o de menos rompe el contrato del modelo. Aqui se
reindexa al catalogo exacto de `matriz_nucleo`, en su orden, y si falta alguna se aborta.

    python scripts/construir_matriz_produccion.py                  # corte en manana
    python scripts/construir_matriz_produccion.py --hasta 2026-09-05
    python scripts/construir_matriz_produccion.py --verificar      # sin reconstruir
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "ingesta", "ingesta/dt_maestro_sergio/v5_master_and_models"):
    sys.path.insert(0, str(REPO / p))

ORO = REPO / "data" / "gold"
REFERENCIA = ORO / "matriz_nucleo"                 # la congelada, solo se lee
SALIDA = ORO / "matriz_produccion"
CACHE = REPO / "data" / "bronze" / "matriz_cruda_produccion.parquet"


def columnas_de_referencia():
    cat = ORO / "matriz_nucleo_columnas.csv"
    if not cat.exists():
        raise FileNotFoundError(f"falta {cat}: es el contrato de columnas")
    return pd.read_csv(cat)["variable"].tolist()


def _hash(d: pd.DataFrame) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(d, index=True).values.tobytes()).hexdigest()[:8]


def _ultimo_con_precio(verbose=True) -> date:
    """El ultimo dia que puede aparecer como target, que NO es manana.

    `construir_dataset_horario` termina con

        df = df[df["target_price"].notna()]

    y el precio de manana no existe todavia -- es justo lo que se quiere predecir --, asi
    que esa fila se descarta siempre, se ponga lo que se ponga en MODELO_END. Tiene sentido
    para entrenar y es justo lo contrario de lo que necesita produccion.

    Mientras eso siga asi, el corte util es el ultimo dia con precio publicado. Con el se
    puede predecir todo lo que va del test hasta hoy: dias que los modelos no han visto y
    que tampoco estan en el tramo de evaluacion, o sea metrica de produccion de verdad.

    Para predecir un dia sin precio hace falta que el constructor del equipo acepte no
    exigir target. Es una linea suya, no nuestra.
    """
    from config import load_config
    import psycopg2
    _, db = load_config()
    with psycopg2.connect(**db) as con, con.cursor() as cur:
        cur.execute("SELECT MAX(datetime)::date FROM spot_price WHERE es_esios IS NOT NULL")
        d = cur.fetchone()[0]
    if verbose:
        print(f"ultimo dia con PMD publicado: {d}  (corte de esta matriz)")
        manana = date.today() + timedelta(days=1)
        if d < manana:
            print(f"  para llegar a {manana} haria falta que el constructor del equipo")
            print(f"  no exigiera target. Ver la nota de `_ultimo_con_precio`.")
    return d


def construir_produccion(hasta: date, verbose=True, usar_cache=False):
    import construir_dataset_maestro_sergio_v5 as v5
    from construir_matriz import construir
    import apagon
    import depurar_matriz

    # El corte del equipo, movido solo para esta pasada. `DATASET_END` es hasta donde se
    # LEE de la base y tiene que ir por delante del objetivo: la fila de D necesita el
    # precio de D+1 como target, y los lags D-1/D-7 necesitan margen por detras.
    antes = (v5.MODELO_END, v5.DATASET_END, v5.EXIGIR_TARGET,
             v5.ERA5_PREFERIR_ECMWF, v5.ESPINA_CALENDARIO)
    v5.MODELO_END = hasta
    v5.DATASET_END = str(hasta + timedelta(days=1))
    # Donde hay prevision, manda la prevision. ERA5 es reanalisis con 5 dias de retraso:
    # a las 11:00 del dia D no existe el de D-1 ni el de D-2, asi que entrenar con el seria
    # entrenar con algo que en produccion no se tiene. Esto tiene que ir activado TAMBIEN
    # al construir la matriz de entrenamiento, o las dos dejan de ser comparables.
    v5.ERA5_PREFERIR_ECMWF = True
    # La base sale del pivot de precios, asi que sin esto no existe la fila del dia
    # que se quiere predecir -- no tiene precio todavia, que es el motivo de predecirlo.
    v5.ESPINA_CALENDARIO = True
    # Si se pide un dia cuyo precio aun no existe -- manana --, hay que dejar de exigir
    # target: si no, esa fila se cae y no hay nada que predecir. Solo se relaja cuando
    # hace falta, para que los dias con precio sigan comprobandose como siempre.
    v5.EXIGIR_TARGET = hasta <= _ultimo_con_precio(verbose=False)
    if verbose:
        print(f"corte movido para esta pasada:")
        print(f"    MODELO_END     {antes[0]}  ->  {v5.MODELO_END}   (ultimo dia predicho)")
        print(f"    DATASET_END    {antes[1]}  ->  {v5.DATASET_END}   (hasta donde se lee)")
        print(f"    EXIGIR_TARGET  {antes[2]}  ->  {v5.EXIGIR_TARGET}"
              f"{'   (el dia pedido aun no tiene precio)' if not v5.EXIGIR_TARGET else ''}")
        print(f"    PREFERIR_ECMWF {antes[3]}  ->  {v5.ERA5_PREFERIR_ECMWF}"
              f"   (los lags meteo salen de la prevision, no del reanalisis)")
        print(f"    ESPINA_CALEND. {antes[4]}  ->  {v5.ESPINA_CALENDARIO}"
              f"   (la fila del dia a predecir no depende de que haya precio)")
        print()

    try:
        datos = construir(cache=CACHE, forzar=not usar_cache, verbose=verbose)
    finally:
        (v5.MODELO_END, v5.DATASET_END, v5.EXIGIR_TARGET,
         v5.ERA5_PREFERIR_ECMWF, v5.ESPINA_CALENDARIO) = antes   # no dejar el modulo tocado

    datos, _ = apagon.imputar(datos, verbose=verbose)
    datos, _ = depurar_matriz.depurar(datos, verbose=verbose)

    # `ts` no sale de la depuracion: lo anade el notebook 04 al escribir las matrices, y
    # es columna de control, no entrada del modelo. Se reconstruye igual -- verificado
    # contra matriz_nucleo, donde ts == fecha_objetivo + hora en las 57.521 filas.
    if "ts" not in datos.columns:
        datos = datos.copy()          # evita el aviso de fragmentacion de pandas
        datos["ts"] = (pd.to_datetime(datos["fecha_objetivo"])
                       + pd.to_timedelta(datos["hora"], unit="h"))
        if verbose:
            print("\n  `ts` reconstruido como fecha_objetivo + hora")

    # ── el contrato de columnas ────────────────────────────────────────────────
    cols = columnas_de_referencia()
    faltan = [c for c in cols if c not in datos.columns]
    if faltan:
        raise RuntimeError(
            f"faltan {len(faltan)} columnas que `matriz_nucleo` si tiene: {faltan[:8]}"
            f"{' ...' if len(faltan) > 8 else ''}\n"
            "  Sin ellas el modelo recibiria una entrada distinta de la que aprendio.\n"
            "  Suele significar que una fuente esta caida: mira que tabla las alimenta.")
    sobran = [c for c in datos.columns if c not in cols]
    datos = datos[cols]
    if verbose and sobran:
        print(f"\n  {len(sobran)} columnas nuevas descartadas para respetar el contrato")

    return _empalmar(datos, verbose)


def _empalmar(datos: pd.DataFrame, verbose=True) -> pd.DataFrame:
    """El pasado se copia de `matriz_nucleo`; solo las filas nuevas salen de esta pasada.

    POR QUE NO VALE RECONSTRUIR EL PASADO
    El canal `*_meteo` lleva pseudo-prevision donde no hay archivo de ECMWF, y esa
    pseudo-prevision se calibra midiendo el error sobre el solape disponible y
    remuestreando de un fondo de dias. Con un mes mas de datos el fondo crece (850 -> 855
    dias de molde) y el error medido cambia (ssrd rmse 141,92 -> 142,27), asi que el
    relleno sale DISTINTO tambien para los dias de train.
    Medido: 6 columnas meteo difieren en el solape si se reconstruye.

    Train distinto significa escaladores distintos, y escaladores distintos significa que
    el modelo recibe la entrada en otra escala y devuelve numeros plausibles y equivocados.

    La matriz congelada es el dato con el que se entreno: para su rango, es la verdad. Aqui
    se copia entera y solo se anaden por la cola los dias que no tenia.
    """
    ref = _leer_referencia()[list(datos.columns)]
    corte = pd.Timestamp(ref["fecha_objetivo"].max())
    nuevas = datos[pd.to_datetime(datos["fecha_objetivo"]) > corte]
    if verbose:
        print(f"\n  empalme: {len(ref):,} filas de matriz_nucleo (hasta {corte:%Y-%m-%d}) "
              f"+ {len(nuevas):,} nuevas")
    return (pd.concat([ref, nuevas], ignore_index=True)
            .sort_values(["fecha_objetivo", "hora"]).reset_index(drop=True))


def _leer_referencia() -> pd.DataFrame:
    """`matriz_nucleo` en el formato que se pueda leer aqui.

    El parquet lo escribio WSL con pyarrow 25 y el pyarrow 19 de Windows no lo abre
    ("Repetition level histogram size mismatch"). El CSV tiene lo mismo y lo lee todo el
    mundo, asi que sirve de red.
    """
    try:
        return pd.read_parquet(f"{REFERENCIA}.parquet")
    except Exception as e:
        print(f"  (el parquet de referencia no se puede leer aqui: {type(e).__name__};"
              f" se usa el CSV)")
        return pd.read_csv(f"{REFERENCIA}.csv", parse_dates=["fecha_pred", "fecha_objetivo", "ts"])


def verificar(prod: pd.DataFrame, verbose=True):
    """El solape con `matriz_nucleo` tiene que ser identico, valor a valor.

    Es la comprobacion que de verdad importa: si los dias comunes coinciden, entonces train
    coincide, y si train coincide los escaladores son los mismos y el modelo sigue siendo
    valido. Comparar solo el numero de columnas no lo garantiza.
    """
    ref = _leer_referencia()
    corte = ref["fecha_objetivo"].max()
    a = ref.sort_values(["fecha_objetivo", "hora"]).reset_index(drop=True)
    b = (prod[prod["fecha_objetivo"] <= corte]
         .sort_values(["fecha_objetivo", "hora"]).reset_index(drop=True))

    print(f"\n  solape hasta {pd.Timestamp(corte).date()}: "
          f"referencia {len(a):,} filas · produccion {len(b):,}")
    if len(a) != len(b):
        print("  DISTINTO NUMERO DE FILAS -- el solape no es comparable")
        return False
    if list(a.columns) != list(b.columns):
        print("  las columnas no estan en el mismo orden")
        return False

    dif = []
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            import numpy as np
            if not np.allclose(x.fillna(-9e9), y.fillna(-9e9), rtol=1e-6, atol=1e-6):
                dif.append(c)
        elif not x.equals(y):
            dif.append(c)
    if dif:
        print(f"  {len(dif)} columnas DIFIEREN en el solape: {dif[:10]}")
        print("  Los modelos NO son validos sobre esta matriz: los escaladores cambiarian.")
        return False
    print("  identico en el solape -> mismos dias de train, mismos escaladores,")
    print("  los modelos guardados siguen siendo validos.")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hasta", help="ultimo dia predicho (por defecto, manana)")
    ap.add_argument("--verificar", action="store_true",
                    help="solo comprueba la matriz ya escrita, sin reconstruir")
    ap.add_argument("--usar-cache", action="store_true",
                    help="reaprovecha data/bronze/matriz_cruda_produccion.parquet en vez "
                         "de releer las 15 tablas (minutos). Solo si es de hoy.")
    ap.add_argument("--silencio", action="store_true")
    a = ap.parse_args()
    v = not a.silencio

    if a.verificar:
        p = Path(f"{SALIDA}.parquet")
        if not p.exists():
            raise SystemExit(f"no existe {p}: construyela primero")
        verificar(pd.read_parquet(p), v)
        return

    hasta = pd.Timestamp(a.hasta).date() if a.hasta else _ultimo_con_precio(v)
    datos = construir_produccion(hasta, verbose=v, usar_cache=a.usar_cache)

    ok = verificar(datos, v)
    h = _hash(datos)
    datos.to_parquet(f"{SALIDA}.parquet", index=False)
    datos.to_csv(f"{SALIDA}.csv", index=False)

    ref_meta = json.loads(Path(f"{REFERENCIA}.meta.json").read_text(encoding="utf-8"))
    Path(f"{SALIDA}.meta.json").write_text(json.dumps({
        "nombre": "produccion",
        "hash": h,
        "generada": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "modelo_end": str(hasta),
        "filas": len(datos), "columnas": datos.shape[1],
        "ventana": f"{datos.fecha_objetivo.min():%Y-%m-%d} -> {datos.fecha_objetivo.max():%Y-%m-%d}",
        "train_end": ref_meta.get("train_end"), "val_end": ref_meta.get("val_end"),
        "derivada_de": {"matriz": "nucleo", "hash": ref_meta.get("hash")},
        "solape_identico": ok,
        "catalogo_columnas": "matriz_nucleo_columnas.csv",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    if v:
        print(f"\n{len(datos):,} filas x {datos.shape[1]} columnas · hash {h}")
        print(f"  {datos.fecha_objetivo.min():%Y-%m-%d} -> {datos.fecha_objetivo.max():%Y-%m-%d}")
        print(f"  escrito en {SALIDA}.parquet")
        if not ok:
            print("\n  OJO: el solape NO es identico. No predigas con esta matriz hasta")
            print("  entender por que -- mira la lista de columnas que difieren.")


if __name__ == "__main__":
    main()
