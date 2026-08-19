"""
TEST 702 — El efecto del autoconsumo en las series de ESIOS desde dic-2025
===========================================================================
Pregunta que resuelve este test:

    ¿Que series de ESIOS dejaron de ser homogeneas cuando REE empezo a
    incorporar la estimacion de autoconsumo, y cuanto afecta al dataset?

DE DONDE SALE LA SOSPECHA
Dos afirmaciones independientes, hechas por dos personas distintas y sobre
columnas distintas, describen el mismo fenomeno:

  load_inter_pipeline.py (18-ago)
      "ree_load incorpora la estimacion de autoconsumo desde dic-2025 y deja
       de ser homogenea. entsoe_load mide lo mismo en todo el rango 2020-2026."

  matriz_generacion_esios_entsoe.xlsx (19-ago), fila de solar
      "B16 = FV + termosolar. ESIOS separa pero su FV incorpora autoconsumo
       desde dic-2025."

Si las dos son ciertas, no es un problema de una columna: es un CRITERIO
GENERAL. Cualquier serie de ESIOS que incorpore autoconsumo cambia de
definicion en dic-2025 y no se puede usar como si fuera homogenea.

POR QUE IMPORTA AHORA
construir_dataset_maestro.py usa hoy las dos versiones contaminadas:

    COLS_DEMANDA_REAL = ["ree_load"]                          <- afectada
    COLS_SOLAR_REAL   = ["ree_gsolar_mw", "ree_gsolter_mw"]   <- la primera, afectada

Y el split pone el test en 2026, entero despues del cambio. Se estaria
entrenando con una definicion de demanda y de solar, y evaluando con otra.
No falla nada, no salta ningun aviso, y las metricas salen mal sin motivo
aparente.

AVISO SOBRE EL ESQUEMA (19-ago-2026)
esios_load_inter ya no existe: la sustituyo load_inter, que trae las dos
demandas en la misma tabla. Consecuencia inmediata que hay que avisar al
equipo: construir_dataset_maestro.py lee de esios_load_inter en dos sitios
(demanda real y NTC), asi que ahora mismo NO SE PUEDE EJECUTAR.

QUE COMPRUEBA
  1. DEMANDA  : load_inter.ree_load vs load_inter.entsoe_load
  2. SOLAR FV : ree_gsolar_mw      vs (entsoe.solar_mw - ree_gsolter_mw)
  3. Si las dos rompen en la MISMA fecha (seria la prueba de que es el mismo
     cambio administrativo y no dos casualidades).
  4. Si la diferencia se comporta como autoconsumo fotovoltaico: casi nula de
     noche, maxima a mediodia, correlacionada con la solar.
  5. Cuanto de cada tramo del split queda afectado.

USO
    python TEST_702_efecto_autoconsumo_esios.py
    python TEST_702_efecto_autoconsumo_esios.py --corte 2025-12-01
    python TEST_702_efecto_autoconsumo_esios.py --grafico
"""

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

for ruta in [Path(__file__).parent.parent, Path(__file__).parent, Path("ingesta")]:
    if (ruta / "config.py").exists():
        sys.path.append(str(ruta))
        break
from config import load_config


# OJO — esios_load_inter YA NO EXISTE (comprobado 19-ago-2026). La sustituye
# load_inter, que trae las dos demandas en la misma tabla: ree_load (ESIOS) y
# entsoe_load (ENTSO-E). Se lee ademas actual_load_mw de entsoe_load_inter como
# respaldo, por si entsoe_load todavia no tiene historico completo: la tabla
# unificada se creo el 18-ago y no tiene cargador historico propio.
SQL = """
    SELECT l.datetime,
           l.ree_load                      AS dem_esios,
           l.entsoe_load                   AS dem_entsoe_nueva,
           n.actual_load_mw                AS dem_entsoe_vieja,
           g.ree_gsolar_mw                 AS fv_esios,
           g.ree_gsolter_mw                AS termosolar,
           s.solar_mw                      AS solar_b16
    FROM load_inter l
    LEFT JOIN entsoe_load_inter n ON n.datetime = l.datetime
    LEFT JOIN esios_gen         g ON g.datetime = l.datetime
    LEFT JOIN entsoe_gen_data   s ON s.datetime = l.datetime
    ORDER BY l.datetime
"""


def cargar():
    _, db = load_config()
    conn = psycopg2.connect(**db)
    try:
        df = pd.read_sql(SQL, conn)
    finally:
        conn.close()

    # Cual de las dos fuentes de demanda ENTSO-E tiene mas cobertura
    cob_nueva = df["dem_entsoe_nueva"].notna().sum()
    cob_vieja = df["dem_entsoe_vieja"].notna().sum()
    print(f"  Cobertura load_inter.entsoe_load        : {cob_nueva:,} horas".replace(",", "."))
    print(f"  Cobertura entsoe_load_inter.actual_load : {cob_vieja:,} horas".replace(",", "."))
    df["dem_entsoe"] = df["dem_entsoe_nueva"].fillna(df["dem_entsoe_vieja"])
    print(f"  -> se usa la combinacion de las dos      : "
          f"{df['dem_entsoe'].notna().sum():,} horas\n".replace(",", "."))

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    local = df["datetime"].dt.tz_convert("Europe/Madrid")
    df["fecha"] = local.dt.date
    df["hora"] = local.dt.hour
    df["mes"] = local.dt.to_period("M").astype(str)

    # FV de ENTSO-E: B16 agrupa FV + termosolar, asi que la FV limpia es la
    # resta. Criterio de la matriz de generacion (19-ago-2026).
    df["fv_entsoe"] = (df["solar_b16"] - df["termosolar"]).clip(lower=0)

    df["dif_dem"] = df["dem_esios"] - df["dem_entsoe"]
    df["dif_fv"] = df["fv_esios"] - df["fv_entsoe"]
    return df


def sec(t):
    print("\n" + "=" * 76)
    print(f"  {t}")
    print("=" * 76)


def bloque_serie(nombre, df, col_a, col_b, col_dif, corte):
    """Analiza una pareja de series y devuelve (media_antes, media_despues)."""
    sub = df.dropna(subset=[col_a, col_b])
    antes = sub[sub.fecha < corte]
    despues = sub[sub.fecha >= corte]

    print(f"\n  --- {nombre} ---")
    print(f"  Horas comparables: {len(sub):,}".replace(",", "."))
    if sub.empty:
        return float("nan"), float("nan")

    for etiqueta, s in [("Antes del corte", antes), ("Desde el corte", despues)]:
        if s.empty:
            continue
        print(f"    {etiqueta:<18} corr {s[col_a].corr(s[col_b]):.5f}   "
              f"dif media {s[col_dif].mean():>8.1f} MW   "
              f"mediana {s[col_dif].median():>8.1f}   desv {s[col_dif].std():>7.1f}")
    ma = antes[col_dif].mean() if not antes.empty else float("nan")
    md = despues[col_dif].mean() if not despues.empty else float("nan")
    print(f"    {'SALTO':<18} {md - ma:>8.1f} MW")
    return ma, md


def primer_mes_roto(df, col_dif, umbral):
    """Primer mes en que la media mensual de la diferencia supera el umbral
    y ya no vuelve por debajo. Es la fecha en que la serie cambia."""
    m = df.groupby("mes")[col_dif].mean().dropna()
    rotos = m[m.abs() > umbral]
    if rotos.empty:
        return None, m
    # buscamos el primero a partir del cual TODOS superan el umbral
    for mes in rotos.index:
        resto = m[m.index >= mes]
        if (resto.abs() > umbral).all():
            return mes, m
    return rotos.index[0], m


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--corte", default="2025-12-01")
    p.add_argument("--grafico", action="store_true")
    a = p.parse_args()
    corte = pd.Timestamp(a.corte).date()

    df = cargar()

    sec("1 · COBERTURA")
    print(f"  Horas totales : {len(df):,}".replace(",", "."))
    print(f"  Periodo       : {df.datetime.min()}  ->  {df.datetime.max()}")
    print(f"  Corte probado : {corte}")

    sec("2 · ¿CAMBIAN LAS DOS SERIES?")
    dem_a, dem_d = bloque_serie("DEMANDA  (load_inter.ree_load vs demanda de ENTSO-E)",
                                df, "dem_esios", "dem_entsoe", "dif_dem", corte)
    fv_a, fv_d = bloque_serie("SOLAR FV (ree_gsolar_mw vs B16 - termosolar)",
                              df, "fv_esios", "fv_entsoe", "dif_fv", corte)

    sec("3 · ¿ROMPEN EN LA MISMA FECHA?")
    print("  Si las dos series se separan el mismo mes, es el mismo cambio")
    print("  administrativo y no dos casualidades independientes.\n")
    mes_dem, serie_dem = primer_mes_roto(df, "dif_dem", 300)
    mes_fv, serie_fv = primer_mes_roto(df, "dif_fv", 300)
    print(f"  Demanda  — primer mes con desvio sostenido: {mes_dem}")
    print(f"  Solar FV — primer mes con desvio sostenido: {mes_fv}")
    if mes_dem and mes_fv and mes_dem == mes_fv:
        print(f"\n  -> COINCIDEN en {mes_dem}. Es el mismo cambio.")
    elif mes_dem and mes_fv:
        print(f"\n  -> No coinciden exactamente ({mes_dem} vs {mes_fv}). Mirar si es")
        print("     un despliegue escalonado o dos cosas distintas.")

    print("\n  Media mensual de las diferencias (ultimos 18 meses):")
    comp = pd.DataFrame({"demanda": serie_dem, "solar_fv": serie_fv}).round(1)
    print(comp.tail(18).to_string())

    sec("4 · ¿LA DIFERENCIA ES AUTOCONSUMO?")
    print("  El autoconsumo es sobre todo FV de tejado: ~0 de noche, maximo a mediodia.\n")
    d2 = df[df.fecha >= corte]
    perfil = pd.DataFrame({
        "dif_demanda": d2.groupby("hora").dif_dem.mean(),
        "dif_solar_fv": d2.groupby("hora").dif_fv.mean(),
        "solar_real": d2.groupby("hora").fv_entsoe.mean(),
    }).round(1)
    print(perfil.to_string())

    if not d2.empty:
        noche = d2[d2.hora.isin([0, 1, 2, 3, 4, 23])]
        centro = d2[d2.hora.isin([11, 12, 13, 14, 15])]
        print(f"\n  Demanda  — noche {noche.dif_dem.mean():7.1f} MW   "
              f"mediodia {centro.dif_dem.mean():7.1f} MW")
        print(f"  Solar FV — noche {noche.dif_fv.mean():7.1f} MW   "
              f"mediodia {centro.dif_fv.mean():7.1f} MW")
        s = d2.dropna(subset=["fv_entsoe"])
        if len(s) > 100:
            print(f"\n  Correlacion con la solar real:")
            print(f"    diferencia de demanda  : {s.dif_dem.corr(s.fv_entsoe):.4f}")
            print(f"    diferencia de solar FV : {s.dif_fv.corr(s.fv_entsoe):.4f}")
            print("\n  Correlacion alta y perfil de campana = autoconsumo fotovoltaico.")
            print("  Perfil plano = el cambio es otra cosa, hay que investigarlo.")

    sec("5 · IMPACTO EN EL SPLIT DEL DATASET")
    TRAIN_END = pd.Timestamp("2024-12-31").date()
    VAL_END = pd.Timestamp("2025-12-31").date()
    tramos = [("train", df[df.fecha <= TRAIN_END]),
              ("validation", df[(df.fecha > TRAIN_END) & (df.fecha <= VAL_END)]),
              ("test", df[df.fecha > VAL_END])]
    for nombre, t in tramos:
        if t.empty:
            continue
        pct = (t.fecha >= corte).mean() * 100
        print(f"  {nombre:<11} {t.fecha.min()} -> {t.fecha.max()}   "
              f"{len(t):>7,} horas   afectadas: {pct:5.1f}%".replace(",", "."))

    sec("6 · VEREDICTO")
    roto_dem = abs(dem_a) < 150 and abs(dem_d - dem_a) > 300
    roto_fv = abs(fv_a) < 150 and abs(fv_d - fv_a) > 300

    print(f"  Demanda  : {'CAMBIA' if roto_dem else 'sin cambio claro'}"
          f"   ({dem_a:.0f} -> {dem_d:.0f} MW)")
    print(f"  Solar FV : {'CAMBIA' if roto_fv else 'sin cambio claro'}"
          f"   ({fv_a:.0f} -> {fv_d:.0f} MW)\n")

    if roto_dem and roto_fv:
        print("  CONFIRMADO EN LAS DOS SERIES. No es un problema de una columna:")
        print("  es un criterio general. Ninguna serie de ESIOS que incorpore")
        print("  autoconsumo es homogenea en todo el rango 2020-2026.")
        print()
        print("  ACCION SOBRE construir_dataset_maestro.py:")
        print("    COLS_DEMANDA_REAL = ['ree_load']  ->  entsoe_load_inter.actual_load_mw")
        print("    COLS_SOLAR_REAL   = ['ree_gsolar_mw', ...]  ->  FV derivada")
        print("                        GREATEST(0, entsoe.solar_mw - ree_gsolter_mw)")
        print()
        print("  Las versiones de ESIOS se conservan como columnas documentales: su")
        print("  diferencia con las de ENTSO-E ESTIMA EL AUTOCONSUMO PENINSULAR, una")
        print("  magnitud que ninguna fuente publica por separado.")
    elif roto_dem or roto_fv:
        cual = "la demanda" if roto_dem else "la solar"
        print(f"  Solo cambia {cual}. Revisar la otra serie con otra fecha de corte")
        print("  antes de generalizar el criterio.")
    else:
        print("  NO SE APRECIA EL CAMBIO con esta fecha de corte. Probar otras fechas")
        print("  con --corte antes de descartar la hipotesis.")

    if a.grafico:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m = df.set_index("datetime").resample("MS").mean(numeric_only=True)
        fig, ax = plt.subplots(3, 1, figsize=(13, 11))

        ax[0].plot(m.index, m.dif_dem, lw=1.8, color="#c0392b", label="demanda: ESIOS - ENTSO-E")
        ax[0].plot(m.index, m.dif_fv, lw=1.8, color="#d99a4e", label="solar FV: ESIOS - ENTSO-E")
        ax[0].axhline(0, color="#888", lw=.8)
        ax[0].axvline(pd.Timestamp(a.corte, tz="UTC"), color="#2c7fb8", ls=":", lw=1.6,
                      label=f"corte {a.corte}")
        ax[0].set_ylabel("MW"); ax[0].legend(); ax[0].grid(alpha=.3)
        ax[0].set_title("¿Rompen las dos series en la misma fecha?")

        ax[1].plot(m.index, m.dem_esios, lw=1.6, label="ESIOS ree_load")
        ax[1].plot(m.index, m.dem_entsoe, lw=1.6, ls="--", label="ENTSO-E actual_load_mw")
        ax[1].set_ylabel("MW"); ax[1].legend(); ax[1].grid(alpha=.3)
        ax[1].set_title("Demanda peninsular — media mensual")

        ax[2].plot(perfil.index, perfil.dif_demanda, lw=1.8, marker="o", ms=3,
                   color="#c0392b", label="diferencia de demanda")
        ax[2].plot(perfil.index, perfil.dif_solar_fv, lw=1.8, marker="o", ms=3,
                   color="#d99a4e", label="diferencia de solar FV")
        ax2b = ax[2].twinx()
        ax2b.fill_between(perfil.index, perfil.solar_real, alpha=.12, color="#d4a017")
        ax2b.set_ylabel("solar real (MW)", color="#a8801a")
        ax[2].axhline(0, color="#888", lw=.8)
        ax[2].set_xlabel("hora del dia (Madrid)"); ax[2].set_ylabel("MW")
        ax[2].legend(loc="upper left"); ax[2].grid(alpha=.3)
        ax[2].set_title("Perfil horario desde el corte — la sombra es la solar real")

        plt.tight_layout()
        plt.savefig("TEST_702_efecto_autoconsumo_esios.png", dpi=130)
        print("\n  Grafico guardado en TEST_702_efecto_autoconsumo_esios.png")


if __name__ == "__main__":
    main()
