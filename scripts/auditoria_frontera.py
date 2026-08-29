"""Auditoria de la frontera de informacion: comprueba que la matriz solo usa lo que existe.

LA PREGUNTA QUE CONTESTA
A las 11:00 del dia D hay que predecir las 24 horas de D+1. En ese instante, ¿esta publicado
todo lo que la matriz le da al modelo? Si una sola columna describe algo que aun no ha
ocurrido -- o que ha ocurrido pero todavia no se ha publicado -- el modelo entrena con
informacion que en produccion no tendra, y el error medido es mentira.

COMO LO COMPRUEBA
No se fia de los nombres. Para cada columna busca, entre 9 desfases posibles, cual es el dia
cuyo valor en la tabla FUENTE coincide con el de la matriz. El desfase que gana es el dia que
la columna describe realmente, medido y no supuesto -- que es como se detecto que
`es_esios_D` describe el dia D (no D+1) y que los commodities van un dia mas atras de lo
necesario.

Desfase, contado desde `fecha_objetivo` (= D+1):

    0 -> el propio D+1     PROHIBIDO salvo calendario y prevision meteorologica
    1 -> el dia D          publicado la tarde de D-1: la casacion a las 13:00, el PBF a las 13:45
    2 -> D-1               la generacion real; a las 11:00 el dia D aun no ha terminado
    7 -> D-6

LAS CUATRO EXCEPCIONES LEGITIMAS AL DESFASE 0
    calendario `d1_*`   es determinista: hoy ya se sabe que el 1 de mayo de 2027 es sabado
    `*_meteo`           es una PREVISION de D+1, no el dato realizado
    `capdisp_*`         es una DECLARACION PREVIA de indisponibilidades, publicada por
                        adelantado; la tabla va un dia por delante de las demas
    banderas            metadatos de trazabilidad, no entran al modelo

TOLERANCIA. Las coincidencias no salen al 100 % y eso es normal: hay horas repetidas por el
cambio de hora, huecos rellenados en la depuracion y, en la meteorologia, interpolacion
horaria sobre una fuente trihoraria. Lo que importa es que el desfase GANADOR este muy por
encima del resto -- si un desfase saca 99 % y los demas 0,2 %, la alineacion es inequivoca.

Uso:
    python scripts/auditoria_frontera.py                    # sobre data/gold/matriz_nucleo
    python scripts/auditoria_frontera.py --matriz completa
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))

TZ = "Europe/Madrid"
DESFASES = range(0, 9)

# Columnas que describen D+1 de forma LEGITIMA y por tanto no son fuga.
#
#   capdisp_*  la potencia disponible es una DECLARACION PREVIA de indisponibilidades, no
#              un dato realizado: los productores la comunican por adelantado y ESIOS la
#              publica antes de que el mercado case. Se comprueba solo: la tabla va un dia
#              por delante de `esios_pbf_gen` y de `spot_price`, asi que a las 11:00 del dia
#              D la fila de D+1 ya existe. Lo unico pendiente es operativo -- el cron la
#              ingesta a las 21:05 -- y esta anotado aparte.
D1_LEGITIMO = ("capdisp_",)

# prefijo de la matriz -> (tabla, como derivar el campo fuente)
BLOQUES = [
    ("pdbc_",  "esios_pdbc_gen",           lambda c: c[len("pdbc_"):-2]),
    ("pbfli_", "esios_pbf_load_inter",     lambda c: c[len("pbfli_"):-2]),
    ("bil_",   "esios_pbf_bilateral",      lambda c: c[:-2]),
    ("capdisp_", "esios_capacity_available", lambda c: c[len("capdisp_"):]),
]
# el bombeo y los agregados salen del PBF, no de la tabla derivada (ver pdbc_horario.py)
DESDE_PBF = ("pdbc_pumping_gen_mw_D", "pdbc_pumping_cons_mw_D",
             "pdbc_total_gen_mw_D", "pdbc_unavailable_power_mw_D")

EXENTAS = ("d1_", "hora", "fecha_", "ts", "split", "imputado_apagon",
           "ventana_pisa_apagon", "meteo_es_forecast", "pbf_publicado", "pbf_completo",
           "target_price", "prop_missings")


def _con():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def _indexar(df, tcol="datetime"):
    loc = pd.to_datetime(df[tcol], utc=True).dt.tz_convert(TZ)
    df = df.assign(f=loc.dt.date, h=loc.dt.hour)
    return df.drop_duplicates(subset=["f", "h"], keep="first").set_index(["f", "h"])


def desfase_de(col, serie_fuente, fo, horas, valores):
    """Que desfase alinea la columna con su fuente. Devuelve (dias, coincidencia, margen)."""
    r = []
    for k in DESFASES:
        idx = pd.MultiIndex.from_arrays([(fo - pd.Timedelta(days=k)).dt.date, horas])
        ref = serie_fuente.reindex(idx).to_numpy(dtype="float64")
        r.append((k, float(np.isclose(valores, ref, atol=0.01, equal_nan=False).mean())))
    r.sort(key=lambda x: -x[1])
    margen = r[0][1] - r[1][1] if len(r) > 1 else r[0][1]
    return r[0][0], r[0][1], margen


def auditar(matriz: str = "nucleo", verbose: bool = True) -> pd.DataFrame:
    d = pd.read_parquet(REPO / "data" / "gold" / f"matriz_{matriz}.parquet")
    fo = pd.to_datetime(d["fecha_objetivo"])
    con = _con()
    filas = []
    try:
        cache = {}
        for pref, tabla, campo_de in BLOQUES:
            cols = [c for c in d.columns if c.startswith(pref)]
            if not cols:
                continue
            if tabla not in cache:
                cache[tabla] = _indexar(pd.read_sql(f"SELECT * FROM {tabla}", con))
            g = cache[tabla]
            for c in cols:
                campo = campo_de(c)
                fuente, t = tabla, tabla
                if c in DESDE_PBF:
                    if "esios_pbf_gen" not in cache:
                        cache["esios_pbf_gen"] = _indexar(
                            pd.read_sql("SELECT * FROM esios_pbf_gen", con))
                    g2, t = cache["esios_pbf_gen"], "esios_pbf_gen"
                else:
                    g2 = g
                if campo not in g2.columns:
                    continue
                k, p, m = desfase_de(c, g2[campo], fo, d["hora"], d[c].to_numpy(dtype="float64"))
                filas.append({"variable": c, "tabla": t, "describe_D_menos": k,
                              "coincide": round(p * 100, 1), "margen": round(m * 100, 1)})

        # commodities: diarios, indexados por fecha
        cm = pd.read_sql("SELECT * FROM commodities ORDER BY fecha", con)
        cm["f"] = pd.to_datetime(cm["fecha"]).dt.date
        cm = cm.drop_duplicates("f").set_index("f")
        for c in [x for x in d.columns if x.startswith(("gas_", "co2_"))]:
            if c not in cm.columns:
                continue
            r = []
            for k in DESFASES:
                ref = cm[c].reindex((fo - pd.Timedelta(days=k)).dt.date).to_numpy(dtype="float64")
                r.append((k, float(np.isclose(d[c].to_numpy(dtype="float64"), ref,
                                              atol=0.01, equal_nan=False).mean())))
            r.sort(key=lambda x: -x[1])
            filas.append({"variable": c, "tabla": "commodities", "describe_D_menos": r[0][0],
                          "coincide": round(r[0][1] * 100, 1),
                          "margen": round((r[0][1] - r[1][1]) * 100, 1)})
    finally:
        con.close()

    t = pd.DataFrame(filas)
    # veredicto: desfase 0 = describe el propio D+1 = fuga, salvo exentas
    legitima = t["variable"].str.startswith(D1_LEGITIMO)
    t["veredicto"] = np.where(t["describe_D_menos"] == 0,
                              np.where(legitima, "D+1 declarado", "FUGA"), "ok")
    t.loc[t["coincide"] < 40, "veredicto"] = "sin verificar"

    if verbose:
        print(f"MATRIZ: {matriz}   ({len(d):,} filas x {d.shape[1]} columnas)")
        print()
        print("Dia que describe cada bloque, medido contra la tabla fuente:")
        r = (t.groupby(["tabla", "describe_D_menos"])
               .agg(columnas=("variable", "size"), coincide_min=("coincide", "min"),
                    margen_min=("margen", "min")).reset_index())
        print(r.to_string(index=False))
        print()
        leg = t[t.veredicto == "D+1 declarado"]
        if len(leg):
            print(f"Describen D+1 pero de forma legitima ({len(leg)}): "
                  f"{sorted(leg.variable)}")
            print("   son declaracion previa, publicada antes del cierre. Ver D1_LEGITIMO.")
            print()
        malas = t[~t.veredicto.isin(("ok", "D+1 declarado"))]
        if len(malas):
            print(f"REVISAR ({len(malas)}):")
            print(malas.to_string(index=False))
        else:
            print("Ninguna columna describe el dia objetivo. Frontera respetada.")
        no_aud = [c for c in d.select_dtypes("number").columns
                  if c not in set(t["variable"]) and not c.startswith(EXENTAS)]
        print()
        print(f"sin auditar automaticamente ({len(no_aud)}): "
              f"{no_aud[:8]}{' ...' if len(no_aud) > 8 else ''}")
        print("   (precios europeos, generacion real y meteo se comprueban aparte)")
    return t


def auditar_meteo(matriz: str = "nucleo", verbose: bool = True) -> pd.DataFrame:
    """El canal meteorologico: que hay en cada tramo y si esta anclado al dia correcto.

    Dos comprobaciones distintas segun el tramo:

      con prevision   la columna debe ser la prevision de ECMWF, y su error contra el ERA5
                      del propio D+1 debe ser el error tipico de una prevision a 24 h.
      pseudo          la columna debe estar anclada al ERA5 de D+1 (no al de otro dia) y
                      separarse de el con la MISMA magnitud de error. Si se pareciera
                      demasiado seria meteo perfecta; si se pareciera poco, no seria D+1.
    """
    from era5_horario import cargar_era5
    d = pd.read_parquet(REPO / "data" / "gold" / f"matriz_{matriz}.parquet")
    fo = pd.to_datetime(d["fecha_objetivo"])
    g = cargar_era5(verbose=False)
    g["f"] = pd.to_datetime(g["fecha"]).dt.date
    g = g.rename(columns={"hora": "h"}).drop_duplicates(["f", "h"]).set_index(["f", "h"])

    hay_fc = d["meteo_es_forecast"] == 1
    filas = []
    for c in [x for x in d.columns if x.endswith("_meteo")]:
        v = c[:-len("_meteo")]
        if f"{v}_mean" not in g.columns:
            continue
        for k in (0, 1, 2):
            idx = pd.MultiIndex.from_arrays([(fo - pd.Timedelta(days=k)).dt.date, d["hora"]])
            ref = g[f"{v}_mean"].reindex(idx).to_numpy(dtype="float64")
            for tramo, m in (("previsión", hay_fc.to_numpy()), ("pseudo", (~hay_fc).to_numpy())):
                ok = m & ~np.isnan(ref)
                if ok.sum() < 100:
                    continue
                dif = d[c].to_numpy(dtype="float64")[ok] - ref[ok]
                filas.append({"variable": v, "tramo": tramo, "contra_ERA5_de": f"D+1-{k}",
                              "corr": round(float(np.corrcoef(d[c].to_numpy()[ok], ref[ok])[0, 1]), 3),
                              "rmse": round(float(np.sqrt((dif ** 2).mean())), 2)})
    t = pd.DataFrame(filas)
    if verbose and len(t):
        piv = t[t.contra_ERA5_de == "D+1-0"].pivot(index="variable", columns="tramo",
                                                   values=["corr", "rmse"])
        print("Canal meteorologico contra el ERA5 del PROPIO dia objetivo:")
        print(piv.to_string())
        print()
        print("Lectura: las dos columnas de `corr` deben parecerse. La de `previsión` es el")
        print("acierto real de ECMWF a 24 h; la de `pseudo` debe quedarse cerca -- si fuera")
        print("1,000 seria meteo perfecta (fuga), y si fuera baja no estaria anclada a D+1.")
        print()
        lejos = t[(t.contra_ERA5_de != "D+1-0")]
        mejor = t.loc[t.groupby(["variable", "tramo"])["corr"].idxmax()]
        mal = mejor[mejor.contra_ERA5_de != "D+1-0"]
        if len(mal):
            print(f"AVISO: {len(mal)} casos donde otro dia alinea mejor que D+1:")
            print(mal.to_string(index=False))
        else:
            print("Todas las variables alinean mejor con D+1 que con D o D-1: anclaje correcto.")
    return t


def auditar_precios_y_real(matriz: str = "nucleo", verbose: bool = True) -> pd.DataFrame:
    """Los dos bloques que no casan por prefijo: precios europeos y generacion real.

    Los precios llevan sufijo de alineamiento (`_D`, `_Dm1`, `_Dm6`) sobre un nombre de pais,
    y la generacion real viene de tres tablas distintas bajo un solo bloque. Se auditan
    aparte porque el nombre de la columna no basta para deducir el campo fuente.
    """
    d = pd.read_parquet(REPO / "data" / "gold" / f"matriz_{matriz}.parquet")
    fo = pd.to_datetime(d["fecha_objetivo"])
    con = _con()
    filas = []
    try:
        sp = _indexar(pd.read_sql("SELECT * FROM spot_price", con))
        for c in d.columns:
            for suf, _ in (("_Dm6", 6), ("_Dm2", 2), ("_Dm1", 1), ("_D", 0)):
                if not c.endswith(suf):
                    continue
                campo = c[: -len(suf)]
                if campo in sp.columns:
                    k, p, m = desfase_de(c, sp[campo], fo, d["hora"],
                                         d[c].to_numpy(dtype="float64"))
                    filas.append({"variable": c, "tabla": "spot_price",
                                  "describe_D_menos": k, "coincide": round(p * 100, 1),
                                  "margen": round(m * 100, 1)})
                break
        gen = _indexar(pd.read_sql("SELECT * FROM entsoe_gen_data", con))
        for c in d.columns:
            for suf in ("_Dm6", "_Dm1"):
                if not c.endswith(suf):
                    continue
                campo = c[: -len(suf)]
                if campo in gen.columns:
                    k, p, m = desfase_de(c, gen[campo], fo, d["hora"],
                                         d[c].to_numpy(dtype="float64"))
                    filas.append({"variable": c, "tabla": "entsoe_gen_data",
                                  "describe_D_menos": k, "coincide": round(p * 100, 1),
                                  "margen": round(m * 100, 1)})
                break
    finally:
        con.close()

    t = pd.DataFrame(filas)
    if not len(t):
        return t
    t["veredicto"] = np.where(t["describe_D_menos"] == 0, "FUGA", "ok")
    t.loc[t["coincide"] < 40, "veredicto"] = "sin verificar"
    if verbose:
        print("Precios y generacion real, dia que describe cada sufijo:")
        print(t.groupby(["tabla", "describe_D_menos"])
               .agg(columnas=("variable", "size"), coincide_min=("coincide", "min"))
               .reset_index().to_string(index=False))
        mal = t[t.veredicto != "ok"]
        print()
        if len(mal):
            print(f"REVISAR ({len(mal)}):")
            print(mal.to_string(index=False))
        else:
            print("Ninguna describe el dia objetivo.")
    return t


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matriz", default="nucleo")
    a = ap.parse_args()
    auditar(a.matriz)
    print()
    print("=" * 78)
    print()
    auditar_meteo(a.matriz)
    print()
    print("=" * 78)
    print()
    auditar_precios_y_real(a.matriz)
