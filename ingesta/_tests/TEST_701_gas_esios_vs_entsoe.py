"""
TEST 701 — ¿Son comparables el gas de ENTSO-E y el de ESIOS?
=============================================================
Pregunta que resuelve este test:

    entsoe_gen_data.gas_mw (codigo PSR B04) ¿equivale a
    esios_gen.ree_gccgas_mw + esios_gen.ree_gotherthermal_mw?

POR QUE IMPORTA
El docstring de entsoe_daily_pipeline.py dice que B04 "incluye la cogeneracion
de gas, que en España no tiene codigo PSR propio segun el Anexo II del P.O.
3.1". En ESIOS ese mismo perimetro esta partido en dos indicadores:

    550  ree_gccgas_mw        ciclo combinado, SIN cogeneracion
    1297 ree_gotherthermal_mw cogeneracion y resto termico

Si la suma de los dos reproduce el B04, las dos fuentes son intercambiables y
basta elegir una. Si no, cada columna mide un perimetro distinto y hay que
decidir cual entra al dataset y documentarlo — igual que se hizo con la solar,
donde ENTSO-E agrupa en B16 lo que ESIOS separa en FV y termosolar.

El riesgo de no resolverlo: alguien mete las dos columnas pensando que son
tecnologias distintas, o cambia de una a otra a mitad del analisis y los
numeros dejan de cuadrar sin que salte ningun error.

USO
    python TEST_701_gas_esios_vs_entsoe.py
    python TEST_701_gas_esios_vs_entsoe.py --desde 2022-01-01
    python TEST_701_gas_esios_vs_entsoe.py --grafico   # guarda un PNG

Requisitos: pandas, psycopg2-binary, matplotlib (solo para --grafico).
Credenciales: las mismas de ingesta/credentials.json.
"""

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

# config.py vive en ingesta/. Este test puede correr desde ingesta/_tests/ o
# desde donde sea: probamos las rutas habituales.
for ruta in [Path(__file__).parent.parent, Path(__file__).parent, Path("ingesta")]:
    if (ruta / "config.py").exists():
        sys.path.append(str(ruta))
        break
from config import load_config


SQL = """
    SELECT e.datetime,
           e.gas_mw                AS entsoe_gas,
           g.ree_gccgas_mw         AS esios_ccgt,
           g.ree_gotherthermal_mw  AS esios_otras_term
    FROM entsoe_gen_data e
    JOIN esios_gen g ON g.datetime = e.datetime
    WHERE e.datetime >= %(desde)s
      AND e.gas_mw IS NOT NULL
      AND g.ree_gccgas_mw IS NOT NULL
    ORDER BY e.datetime
"""


def cargar(desde):
    _, db = load_config()
    conn = psycopg2.connect(**db)
    try:
        df = pd.read_sql(SQL, conn, params={"desde": desde})
    finally:
        conn.close()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["esios_suma"] = df["esios_ccgt"] + df["esios_otras_term"].fillna(0)
    df["dif_vs_ccgt"] = df["entsoe_gas"] - df["esios_ccgt"]
    df["dif_vs_suma"] = df["entsoe_gas"] - df["esios_suma"]
    return df


def seccion(t):
    print("\n" + "=" * 74)
    print(f"  {t}")
    print("=" * 74)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", default="2020-01-01")
    p.add_argument("--grafico", action="store_true")
    a = p.parse_args()

    df = cargar(a.desde)

    seccion("1 · COBERTURA")
    print(f"  Horas comparables : {len(df):,}".replace(",", "."))
    print(f"  Periodo           : {df.datetime.min()}  ->  {df.datetime.max()}")
    print(f"  Nulos en otras_term: {df.esios_otras_term.isna().sum():,}".replace(",", "."))

    seccion("2 · CORRELACIONES")
    c1 = df.entsoe_gas.corr(df.esios_ccgt)
    c2 = df.entsoe_gas.corr(df.esios_suma)
    print(f"  entsoe_gas  vs  ree_gccgas_mw                : {c1:.5f}")
    print(f"  entsoe_gas  vs  ree_gccgas + ree_gotherthermal: {c2:.5f}")
    print()
    if c2 > c1:
        print(f"  -> La SUMA correlaciona mejor (+{c2-c1:.5f}). Apunta a que B04")
        print("     efectivamente engloba la cogeneracion de gas.")
    else:
        print(f"  -> El ciclo combinado SOLO correlaciona igual o mejor ({c1-c2:+.5f}).")
        print("     Apunta a que B04 NO incluye lo que ESIOS mete en otras termicas.")

    seccion("3 · DIFERENCIAS (MW)")
    for etiqueta, col in [("entsoe_gas - ccgt", "dif_vs_ccgt"),
                          ("entsoe_gas - (ccgt + otras)", "dif_vs_suma")]:
        s = df[col].describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95])
        print(f"\n  {etiqueta}")
        print(f"    media {s['mean']:>9.1f}   mediana {s['50%']:>9.1f}   desv {s['std']:>8.1f}")
        print(f"    p05   {s['5%']:>9.1f}   p95     {s['95%']:>9.1f}")
        print(f"    |dif| media {df[col].abs().mean():>8.1f}   max {df[col].abs().max():>9.1f}")

    seccion("4 · ¿LA DIFERENCIA ES ESTABLE EN EL TIEMPO?")
    print("  Si la diferencia con el ciclo combinado es un OFFSET estable, es la")
    print("  cogeneracion de gas y se puede separar. Si baila, los perimetros no")
    print("  se corresponden y no hay forma limpia de convertir una en otra.\n")
    anual = df.assign(anio=df.datetime.dt.year).groupby("anio").agg(
        horas=("entsoe_gas", "size"),
        entsoe_gas=("entsoe_gas", "mean"),
        esios_ccgt=("esios_ccgt", "mean"),
        esios_otras=("esios_otras_term", "mean"),
        dif_ccgt=("dif_vs_ccgt", "mean"),
        dif_suma=("dif_vs_suma", "mean"),
    ).round(1)
    print(anual.to_string())

    seccion("5 · VEREDICTO")
    med_ccgt = df.dif_vs_ccgt.median()
    med_suma = df.dif_vs_suma.median()
    disp_ccgt = df.dif_vs_ccgt.std()
    disp_suma = df.dif_vs_suma.std()
    rango_anual = anual.dif_ccgt.max() - anual.dif_ccgt.min()

    print(f"  Diferencia mediana frente a ccgt solo : {med_ccgt:8.1f} MW  (desv {disp_ccgt:.1f})")
    print(f"  Diferencia mediana frente a la suma   : {med_suma:8.1f} MW  (desv {disp_suma:.1f})")
    print(f"  Variacion del offset entre años       : {rango_anual:8.1f} MW")
    print()

    if abs(med_suma) < 50 and disp_suma < 200:
        print("  EQUIVALENTES. La suma de las dos columnas de ESIOS reproduce el B04")
        print("  de ENTSO-E. Son intercambiables: elegir una fuente y documentarlo.")
        print("  Recomendacion: ESIOS, porque permite separar ciclo combinado de")
        print("  cogeneracion, y el ciclo combinado es el que marca precio marginal.")
    elif abs(med_ccgt) < 50 and disp_ccgt < 200:
        print("  EQUIVALENTES A CICLO COMBINADO. B04 NO incluye la cogeneracion, al")
        print("  contrario de lo que dice el docstring del pipeline. Habria que")
        print("  corregir ese comentario en el repo.")
    elif rango_anual < 150:
        print("  NO SON EQUIVALENTES, pero la diferencia es un OFFSET ESTABLE.")
        print("  B04 cubre un perimetro mayor que el ciclo combinado, y la diferencia")
        print("  se comporta como una linea base de cogeneracion. Se puede usar una u")
        print("  otra siendo consciente del desfase, pero NO mezclarlas.")
    else:
        print("  NO SON EQUIVALENTES Y LA DIFERENCIA NO ES ESTABLE.")
        print("  Cada fuente mide un perimetro distinto y la relacion cambia con los")
        print("  años. Hay que elegir UNA y no cambiar de fuente a mitad del analisis.")
        print("  Recomendacion: ESIOS, por el desglose ciclo combinado / cogeneracion.")

    print("\n  En cualquier caso: NO meter entsoe_gas y esios_ccgt como si fueran")
    print("  tecnologias distintas. Miden lo mismo con distinto perimetro.")

    if a.grafico:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m = df.set_index("datetime").resample("MS").mean(numeric_only=True)
        fig, ax = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
        ax[0].plot(m.index, m.entsoe_gas, lw=1.7, label="ENTSO-E gas_mw (B04)")
        ax[0].plot(m.index, m.esios_ccgt, lw=1.7, label="ESIOS ree_gccgas_mw")
        ax[0].plot(m.index, m.esios_suma, lw=1.3, ls="--", label="ESIOS ccgt + otras termicas")
        ax[0].set_ylabel("MW"); ax[0].legend(); ax[0].grid(alpha=.3)
        ax[0].set_title("Gas: ENTSO-E vs ESIOS — media mensual")
        ax[1].plot(m.index, m.dif_vs_ccgt, lw=1.7, color="#c0392b", label="entsoe - ccgt")
        ax[1].plot(m.index, m.dif_vs_suma, lw=1.7, color="#2c7fb8", label="entsoe - (ccgt+otras)")
        ax[1].axhline(0, color="#888", lw=.8)
        ax[1].set_ylabel("MW"); ax[1].legend(); ax[1].grid(alpha=.3)
        ax[1].set_title("Diferencia")
        plt.tight_layout()
        plt.savefig("TEST_701_gas_esios_vs_entsoe.png", dpi=130)
        print("\n  Grafico guardado en TEST_701_gas_esios_vs_entsoe.png")


if __name__ == "__main__":
    main()
