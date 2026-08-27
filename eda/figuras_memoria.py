"""
figuras_memoria.py -- genera las 20 figuras seleccionadas y la tabla de decisiones del EDA.

Qué produce
-----------
    docs/figuras/fig01_*.png ... fig20_*.png     las 20 figuras, listas para la memoria
    docs/figuras/pies_de_figura.md               el pie de cada una, con su cifra clave
    docs/decisiones_features.csv                 qué columna entra al modelado y con qué trato
    docs/cifras_clave.md                         los números que van en texto

Por qué en un script y no en un notebook
----------------------------------------
Las figuras de la memoria se regeneran muchas veces: cambia un criterio, se amplía el histórico,
un revisor pide otro corte. Un script se relanza entero y garantiza que las veinte salen del
mismo parquet y con el mismo estilo. Un notebook invita a re-ejecutar celdas sueltas, y ahí es
donde aparecen las figuras que ya no corresponden al texto.

La selección y el motivo de cada figura están en `docs/seleccion_figuras_memoria.md`.

Uso
---
    python eda/figuras_memoria.py
    python eda/figuras_memoria.py --solo 4 6 14 19    # sólo algunas
    python eda/figuras_memoria.py --formato pdf       # vectorial para imprenta
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*PeriodArray.*")

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "figuras"
TZ = "Europe/Madrid"

# Regímenes que no son observaciones extremas sino otro proceso.
APAGON = (pd.Timestamp("2025-04-28", tz="UTC"), pd.Timestamp("2025-05-06 23:00", tz="UTC"))
EXCEPCION = (pd.Timestamp("2022-06-15", tz="UTC"), pd.Timestamp("2023-12-31 23:00", tz="UTC"))
# NOTA: fechas de la excepción ibérica pendientes de verificar contra BOE.

PIES: list[tuple[int, str, str]] = []   # (nº, fichero, pie de figura)
CIFRAS: list[tuple[str, str]] = []      # (concepto, valor) para el texto


# ---------------------------------------------------------------------------
# Estilo e infraestructura
# ---------------------------------------------------------------------------

def estilo():
    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 200, "savefig.bbox": "tight",
        "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
        "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.6,
        "figure.facecolor": "white",
    })


def guardar(fig, n: int, nombre: str, pie: str, fmt: str = "png"):
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"fig{n:02d}_{nombre}.{fmt}"
    fig.savefig(ruta)
    plt.close(fig)
    PIES.append((n, ruta.name, pie))
    print(f"  fig{n:02d}  {ruta.name}")


def cifra(concepto: str, valor):
    CIFRAS.append((concepto, str(valor)))


def pick(d, *c):
    for x in c:
        if x is not None and x in d.columns:
            return x
    return None


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def cargar() -> pd.DataFrame:
    p = RAIZ / "data" / "bronze" / "bronze_unificado.parquet"
    if not p.exists():
        sys.exit(f"No existe {p}. Correr antes: python eda/union_bronze.py")

    df = pd.read_parquet(p)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)

    dup = df["ts_utc"].duplicated().sum()
    if dup:
        sys.exit(f"{dup:,} horas repetidas en el parquet. Regenerar antes de figurar nada.")

    df["ts_local"] = df["ts_utc"].dt.tz_convert(TZ)
    df["hora"] = df["ts_local"].dt.hour
    df["dia"] = df["ts_local"].dt.date
    df["anio"] = df["ts_local"].dt.year
    df["mes_p"] = df["ts_local"].dt.to_period("M")
    df["regimen"] = np.select(
        [df["ts_utc"].between(*APAGON), df["ts_utc"].between(*EXCEPCION)],
        ["apagón", "excepción ibérica"], default="normal")
    print(f"Parquet: {len(df):,} horas x {df.shape[1]} columnas")
    return df


def columnas(df) -> dict:
    """Resuelve una vez los nombres reales. Si falta el precio, no hay memoria que escribir."""
    c = {
        "precio":     pick(df, "spot_es_esios", "spot_es_omie", "spot_es_entsoe"),
        "precio_fr":  pick(df, "spot_fr_entsoe"),
        "precio_pt":  pick(df, "spot_pt_omie", "spot_pt_entsoe"),
        "fc_demanda": pick(df, "forecast_demanda_mercado_prev_mw", "fc_ree_demanda_prev"),
        "fc_eolica":  pick(df, "forecast_gen_wind_prev_mw", "fc_ree_gwind_prev"),
        "fc_solar":   pick(df, "forecast_gen_solar_pv_prev_mw", "fc_ree_gsolar_prev"),
        "demanda":    pick(df, "load_inter_entsoe_load"),
        "eolica":     pick(df, "entsoe_wind_mw", "gen_ent_gwind"),
        "solar":      pick(df, "calc_solar_fv_mw", "gen_c_gsolar"),
        "hidro":      pick(df, "calc_hydro_dispatch_mw", "gen_c_ghydrodispatch"),
        "ccgt":       pick(df, "esios_gen_ree_gccgas_mw", "gen_ree_gccgt"),
        "gas":        pick(df, "tp_ttf_cierre", "commodities_gas_ttf_m1"),
        "gas_mib":    pick(df, "commodities_gas_mibgas"),
        "t2m":        pick(df, "era5_t2m_mean"),
        "autocons":   pick(df, "calc_autoconsumo_mw"),
        "solar_ree":  pick(df, "esios_gen_ree_gsolar_mw"),
        "gas_ent":    pick(df, "entsoe_gas_mw"),
        "otherth":    pick(df, "esios_gen_ree_gotherthermal_mw"),
    }
    if c["precio"] is None:
        sys.exit("Falta spot_price en el bronce. Sin target no hay figuras del target.")
    faltan = [k for k, v in c.items() if v is None]
    if faltan:
        print(f"Aviso: sin resolver {faltan} -- las figuras que dependan se omiten.")
    return c


# ---------------------------------------------------------------------------
# BLOQUE 1 · Qué hay que predecir
# ---------------------------------------------------------------------------

def fig01(df, C, fmt):
    diario = df.groupby("dia")[C["precio"]].mean()
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(pd.to_datetime(diario.index), diario.values, lw=0.7, color="#1f4e79")
    ax.axvspan(EXCEPCION[0].tz_localize(None), EXCEPCION[1].tz_localize(None),
               alpha=0.15, color="orange", label="excepción ibérica")
    ax.axvspan(APAGON[0].tz_localize(None), APAGON[1].tz_localize(None),
               alpha=0.40, color="red", label="apagón ibérico")
    ax.set_ylabel("EUR/MWh"); ax.set_xlabel("")
    ax.set_title("Precio medio diario del mercado español, con los regímenes marcados")
    ax.legend(frameon=False, ncol=2)

    med = df.groupby("regimen")[C["precio"]].mean().round(2)
    for k, v in med.items():
        cifra(f"Precio medio · régimen {k}", f"{v} EUR/MWh")
    guardar(fig, 1, "precio_diario_regimenes",
            f"Precio medio diario. La excepción ibérica promedia "
            f"{med.get('excepción ibérica', float('nan')):.2f} EUR/MWh frente a "
            f"{med.get('normal', float('nan')):.2f} en régimen normal.", fmt)


def fig02(df, C, fmt):
    p = df[C["precio"]].dropna()
    fig, axes = plt.subplots(1, 2, figsize=(9, 3))
    axes[0].hist(p, bins=120, color="#2b7bba")
    axes[0].set_title("Distribución del precio horario"); axes[0].set_xlabel("EUR/MWh")
    axes[1].hist(p, bins=120, color="#2b7bba"); axes[1].set_yscale("log")
    axes[1].set_title("Misma distribución, eje logarítmico"); axes[1].set_xlabel("EUR/MWh")

    cifra("Asimetría del precio", round(p.skew(), 2))
    cifra("Curtosis del precio", round(p.kurtosis(), 2))
    cifra("Rango del precio", f"[{p.min():.0f}, {p.max():.0f}] EUR/MWh")
    lado = "derecha" if p.skew() > 0 else "izquierda"
    guardar(fig, 2, "distribucion_precio",
            f"Distribución del precio horario. Asimetría {p.skew():.2f} (cola más larga por "
            f"la {lado}) y curtosis {p.kurtosis():.2f}. Las dos colas importan por motivos "
            f"distintos: la alta domina el error medio, la baja el valor del arbitraje.", fmt)


def fig03(df, C, fmt):
    bins = [-np.inf, 0, 0.001, 5, 50, 100, 200, np.inf]
    etq = ["negativo", "cero", "0-5", "5-50", "50-100", "100-200", ">200"]
    r = pd.cut(df[C["precio"]], bins=bins, labels=etq, right=False)
    t = pd.crosstab(df["anio"], r)

    fig, ax = plt.subplots(figsize=(9, 3.4))
    (t.div(t.sum(axis=1), axis=0) * 100).plot(kind="bar", stacked=True, ax=ax, width=0.8,
                                              colormap="RdYlBu_r")
    ax.set_ylabel("% de horas del año"); ax.set_xlabel("")
    ax.set_title("Reparto de horas por rango de precio y año")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", frameon=False, title="EUR/MWh")

    neg = t["negativo"] if "negativo" in t else pd.Series(dtype=int)
    cifra("Horas de precio negativo por año", neg.to_dict())
    guardar(fig, 3, "rangos_precio_anio",
            f"Reparto por rango de precio. Las horas de precio negativo pasan de "
            f"{neg.iloc[0] if len(neg) else 0} en {t.index[0]} a {neg.iloc[-1] if len(neg) else 0} "
            f"en {t.index[-1]}: un fenómeno nuevo, no una cola de la distribución.", fmt)


def fig04(df, C, fmt):
    normal = df[df["regimen"] == "normal"]
    perfil = normal.pivot_table(index="hora", columns="anio", values=C["precio"], aggfunc="mean")

    fig, ax = plt.subplots(figsize=(9, 3.6))
    perfil.plot(ax=ax, colormap="viridis", lw=1.4)
    ax.set_xlabel("Hora local (Europe/Madrid)"); ax.set_ylabel("EUR/MWh")
    ax.set_xticks(range(0, 24, 2))
    ax.set_title("Perfil horario del precio por año (excluye apagón y excepción ibérica)")
    ax.legend(title="año", frameon=False, ncol=2)

    amp = (perfil.max() - perfil.min()).round(1)
    hmin = perfil.idxmin()
    cifra("Amplitud del perfil horario por año", amp.to_dict())
    cifra("Hora local del mínimo por año", hmin.to_dict())
    guardar(fig, 4, "perfil_horario_precio_anio",
            f"Perfil horario por año. La hora del mínimo se desplaza de las "
            f"{hmin.iloc[0]}h a las {hmin.iloc[-1]}h y la amplitud crece de "
            f"{amp.iloc[0]:.0f} a {amp.iloc[-1]:.0f} EUR/MWh: la estructura horaria que el "
            f"modelo debe aprender NO es estacionaria.", fmt)


def fig05(df, C, fmt):
    s = df.set_index("ts_utc")[C["precio"]].asfreq("h")
    acf = [s.corr(s.shift(k)) for k in range(1, 193)]

    fig, ax = plt.subplots(figsize=(9, 2.8))
    ax.bar(range(1, 193), acf, width=1.0, color="#2b7bba")
    for k in range(24, 193, 24):
        ax.axvline(k, color="grey", lw=0.6, ls="--")
    ax.set_xlabel("desfase (horas)"); ax.set_ylabel("correlación")
    ax.set_title("Autocorrelación del precio horario hasta 8 días")

    d = df.groupby("dia")[C["precio"]].mean()
    d.index = pd.to_datetime(d.index)
    cifra("Autocorrelación horaria D-1", round(s.corr(s.shift(24)), 4))
    cifra("Autocorrelación diaria D-1", round(d.corr(d.shift(1)), 4))
    guardar(fig, 5, "autocorrelacion_precio",
            f"Autocorrelación del precio. El valor de hace 24 h explica r="
            f"{s.corr(s.shift(24)):.3f} del actual: es la vara mínima que cualquier feature "
            f"exógena tiene que superar.", fmt)


# ---------------------------------------------------------------------------
# BLOQUE 2 · Qué explica el precio
# ---------------------------------------------------------------------------

ETIQUETAS = [("forecast_", "SIN FUGA"), ("fc_", "SIN FUGA"), ("ent_fc_", "SIN FUGA"),
             ("ecmwf_", "SIN FUGA"), ("tp_", "DESFASE D-2"), ("commodities_", "DESFASE D-2"),
             ("era5_", "CON FUGA"), ("entsoe_", "CON FUGA"), ("esios_gen_", "CON FUGA"),
             ("load_inter_", "CON FUGA"), ("gen_", "CON FUGA"), ("pdbc_", "CON FUGA"),
             ("calc_", "CON FUGA"), ("cap_disp_", "CONDICIONAL"),
             ("cap_inst_", "SIN FUGA"), ("spot_", "CON FUGA")]
COLOR_FUGA = {"SIN FUGA": "#2ca02c", "DESFASE D-2": "#f5a623",
              "CON FUGA": "#c0392b", "CONDICIONAL": "#7f7f7f"}


def etiqueta_fuga(col, precio):
    if col == precio:
        return "TARGET"
    for p, v in ETIQUETAS:
        if col.startswith(p):
            return v
    return "CONDICIONAL"


def fig06(df, C, fmt):
    num = df.select_dtypes(include=[np.number]).columns
    num = [c for c in num if c not in ("hora", "anio", "hour_utc", "hour_local", "tensor_index")]
    # Fuera las otras fuentes españolas del precio: son EL MISMO target (r=1,0000), y
    # colarlas en el ranking de drivers sería tautológico.
    gemelas = {"spot_es_esios", "spot_es_omie", "spot_es_entsoe"} - {C["precio"]}
    num = [c for c in num if c not in gemelas]
    normal = df[df["regimen"] == "normal"]
    corr = normal[num].corr()[C["precio"]].drop(C["precio"], errors="ignore").dropna()
    corr = corr.reindex(corr.abs().sort_values(ascending=False).index).head(18)

    fugas = [etiqueta_fuga(c, C["precio"]) for c in corr.index]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.barh(range(len(corr)), corr.values[::-1],
            color=[COLOR_FUGA.get(f, "grey") for f in fugas[::-1]])
    ax.set_yticks(range(len(corr)), [c[:38] for c in corr.index[::-1]])
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("correlación con el precio")
    ax.set_title("Drivers del precio, coloreados por disponibilidad a las 12:00 de D")

    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=v, label=k) for k, v in COLOR_FUGA.items()],
              frameon=False, loc="lower right")

    sin_fuga = corr[[f in ("SIN FUGA", "DESFASE D-2") for f in fugas]]
    if len(sin_fuga):
        cifra("Mejor |r| sin fuga", f"{sin_fuga.abs().max():.4f} ({sin_fuga.abs().idxmax()})")
    cifra("Mejor |r| con fuga", f"{corr.abs().max():.4f} ({corr.abs().idxmax()})")
    guardar(fig, 6, "ranking_drivers_fuga",
            "Correlación de cada driver con el precio. El color indica si el dato existe a "
            "las 12:00 de D, cuando hay que emitir la predicción: casi todo lo que encabeza "
            "el ranking es inutilizable en producción.", fmt)


def fig07(df, C, fmt):
    if not C["gas"]:
        return
    d = df[["regimen", C["precio"], C["gas"]]].dropna()
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    for r, sub in d.groupby("regimen"):
        ax.scatter(sub[C["gas"]], sub[C["precio"]], s=4, alpha=0.35, label=r)
    ax.set_xlabel("precio del gas (EUR/MWh)"); ax.set_ylabel("precio eléctrico (EUR/MWh)")
    ax.set_title("Precio eléctrico frente al gas, por régimen")
    ax.legend(frameon=False, markerscale=3)

    ratio = (d[C["precio"]] / d[C["gas"]].replace(0, np.nan)).groupby(d["regimen"]).median().round(2)
    cifra("Ratio precio/gas por régimen", ratio.to_dict())
    guardar(fig, 7, "precio_vs_gas_regimen",
            f"Precio frente al gas. El ratio mediano cae de {ratio.get('normal', float('nan')):.2f} "
            f"en régimen normal a {ratio.get('excepción ibérica', float('nan')):.2f} bajo el tope "
            f"al gas: dos regímenes distintos, no ruido.", fmt)


def clases_horarias(df, precio, K=4):
    d = df[["dia", "hora", "anio", precio]].dropna(subset=[precio]).copy()
    d["rango"] = d.groupby("dia")[precio].rank(method="first")
    d["n"] = d.groupby("dia")[precio].transform("size")
    d["clase"] = "intermedia"
    d.loc[d["rango"] <= K, "clase"] = "barata"
    d.loc[d["rango"] > d["n"] - K, "clase"] = "cara"
    d["es_cara"] = (d["clase"] == "cara").astype(int)
    return d


def drivers_dict(C):
    return {
        "demanda prevista": (C["fc_demanda"], "SIN FUGA"),
        "eólica prevista":  (C["fc_eolica"], "SIN FUGA"),
        "solar prevista":   (C["fc_solar"], "SIN FUGA"),
        "gas":              (C["gas"], "DESFASE D-2"),
        "demanda real":     (C["demanda"], "CON FUGA"),
        "eólica real":      (C["eolica"], "CON FUGA"),
        "solar FV real":    (C["solar"], "CON FUGA"),
        "hidráulica":       (C["hidro"], "CON FUGA"),
        "ciclo combinado":  (C["ccgt"], "CON FUGA"),
    }


def fig08(df, C, fmt):
    d = clases_horarias(df, C["precio"])
    D = d.merge(df[["dia", "hora"] + [c for c, _ in drivers_dict(C).values() if c]],
                on=["dia", "hora"], how="left")
    dr = {k: v for k, v in drivers_dict(C).items() if v[0]}

    filas = []
    for nombre, (col, fuga) in dr.items():
        a = D.loc[D["clase"] == "cara", col].dropna()
        b = D.loc[D["clase"] == "barata", col].dropna()
        if len(a) < 100 or len(b) < 100:
            continue
        s = np.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
        filas.append({"driver": nombre, "fuga": fuga, "d": (a.mean() - b.mean()) / s if s else 0})
    B = pd.DataFrame(filas).set_index("driver")
    B["abs"] = B["d"].abs()
    B = B.sort_values("abs")

    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.barh(B.index, B["d"], color=[COLOR_FUGA.get(f, "grey") for f in B["fuga"]])
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("d de Cohen  (positivo: más alto en horas caras)")
    ax.set_title("Cuánto separa cada driver las horas caras de las baratas")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=COLOR_FUGA[k], label=k)
                       for k in ("SIN FUGA", "DESFASE D-2", "CON FUGA")],
              frameon=False, loc="lower right")

    usables = B[B["fuga"] != "CON FUGA"]
    cifra("Mejor |d| utilizable", f"{usables['abs'].max():.3f} ({usables['abs'].idxmax()})")
    cifra("Mejor |d| total", f"{B['abs'].max():.3f} ({B['abs'].idxmax()})")
    guardar(fig, 8, "separacion_horas_caras",
            f"Separación entre horas caras y baratas. El mejor driver utilizable alcanza "
            f"d={usables['abs'].max():.2f} frente a {B['abs'].max():.2f} del mejor con fuga. "
            f"La eólica prevista apenas separa (d={B.loc['eólica prevista','abs']:.2f}) pese a "
            f"mover el nivel del precio.", fmt)


def fig09(df, C, fmt):
    try:
        from sklearn.metrics import roc_auc_score, roc_curve
    except ImportError:
        print("  fig09 omitida: falta scikit-learn")
        return
    d = clases_horarias(df, C["precio"])
    dr = {k: v for k, v in drivers_dict(C).items() if v[0]}
    D = d.merge(df[["dia", "hora"] + [c for c, _ in dr.values()]], on=["dia", "hora"], how="left")

    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    resumen = {}
    for nombre, (col, fuga) in dr.items():
        m = D[col].notna()
        if m.sum() < 500:
            continue
        auc = roc_auc_score(D.loc[m, "es_cara"], D.loc[m, col])
        score = D.loc[m, col] if auc >= 0.5 else -D.loc[m, col]
        fpr, tpr, _ = roc_curve(D.loc[m, "es_cara"], score)
        ax.plot(fpr, tpr, "-" if fuga != "CON FUGA" else "--", lw=1.4,
                color=COLOR_FUGA.get(fuga, "grey"),
                label=f"{nombre} ({max(auc, 1-auc):.3f})")
        resumen[nombre] = round(max(auc, 1 - auc), 4)
    ax.plot([0, 1], [0, 1], ls=":", color="grey", lw=1)
    ax.set_xlabel("tasa de falsos positivos"); ax.set_ylabel("tasa de verdaderos positivos")
    ax.set_title("Capacidad de cada driver para identificar una hora cara\n"
                 "(línea continua: utilizable a las 12:00 de D)")
    ax.legend(frameon=False, fontsize=7, loc="lower right")

    cifra("AUC por driver para 'hora cara'", resumen)
    guardar(fig, 9, "roc_hora_cara",
            "Curvas ROC para clasificar una hora como cara. El AUC no depende del umbral, "
            "así que permite comparar drivers con escalas distintas.", fmt)


# ---------------------------------------------------------------------------
# BLOQUE 3 · Qué se puede usar
# ---------------------------------------------------------------------------

def fig10(df, C, fmt):
    filas = [
        ("spot_price (España)", "TARGET", "Se casa a las 12:00 de D y se publica ~12:45"),
        ("spot_price (11 zonas)", "CON FUGA", "Casación SDAC simultánea: el de D+1 no existe"),
        ("forecast (REE)", "SIN FUGA", "Publicada antes de las 11:00 de D-1"),
        ("entsoe_forecast_da", "SIN FUGA", "Segunda previsión, difiere de REE"),
        ("ecmwf_forecast_agg", "SIN FUGA", "Sólo 168 filas: ventana móvil, no histórico"),
        ("esios_capacity_available", "CONDICIONAL", "D-01: valor guardado a las 21:05 de D"),
        ("era5_weather_agg", "CON FUGA", "Reanálisis: el tiempo que ocurrió, no el previsto"),
        ("generation / load_inter", "CON FUGA", "Real: a las 12:00 sólo han pasado las horas 0-11"),
        ("esios_pdbc_gen", "CON FUGA", "Misma casación que el precio: circular"),
        ("commodities / trayport", "DESFASE D-2", "Cierran ~17:30, tras el cierre eléctrico"),
    ]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.axis("off")
    tabla = ax.table(cellText=[[a, b, c] for a, b, c in filas],
                     colLabels=["Tabla", "Veredicto", "Motivo"],
                     cellLoc="left", loc="center",
                     colWidths=[0.26, 0.16, 0.58])
    tabla.auto_set_font_size(False); tabla.set_fontsize(7.5); tabla.scale(1, 1.35)
    for i, (_, v, _) in enumerate(filas, start=1):
        tabla[(i, 1)].set_facecolor(COLOR_FUGA.get(v, "#dddddd"))
        tabla[(i, 1)].set_text_props(color="white" if v != "DESFASE D-2" else "black")
    for j in range(3):
        tabla[(0, j)].set_facecolor("#1f4e79")
        tabla[(0, j)].set_text_props(color="white", weight="bold")
    ax.set_title("Frontera de fuga: qué dato existe a las 12:00 de D", pad=14)
    guardar(fig, 10, "frontera_fuga",
            "Clasificación de cada tabla según esté disponible en el momento de emitir la "
            "predicción. Es la tabla que gobierna la selección de variables.", fmt)


def fig11(df, C, fmt):
    if not (C["precio_pt"] and C["precio_fr"]):
        return
    UMBRAL = 0.01
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.2), sharey=True)
    for ax, (pais, col) in zip(axes, [("Portugal", C["precio_pt"]), ("Francia", C["precio_fr"])]):
        d = df[["anio", C["precio"], col]].dropna()
        dif = (d[C["precio"]] - d[col]).abs()
        acopl = (dif <= UMBRAL).groupby(d["anio"]).mean().mul(100)
        ax.bar(acopl.index.astype(str), acopl.values, color="#2ca02c")
        ax.set_title(f"España – {pais}")
        ax.set_ylabel("% de horas acopladas" if pais == "Portugal" else "")
        ax.set_ylim(0, 100)
        cifra(f"% horas acopladas con {pais}", acopl.round(1).to_dict())
    fig.suptitle("Acoplamiento de precios: horas en que la interconexión no se satura", y=1.02)
    guardar(fig, 11, "acoplamiento_precios",
            "Porcentaje de horas con precio idéntico al del país vecino. Con Portugal "
            "verifica el supuesto de zona única peninsular; con Francia mide cuánta "
            "información del precio francés es independiente.", fmt)


def fig12(df, C, fmt):
    dr = {k: v[0] for k, v in drivers_dict(C).items() if v[0]}
    por_hora = pd.DataFrame({
        k: df.groupby("hora").apply(lambda g: g[C["precio"]].corr(g[v]), include_groups=False)
        for k, v in dr.items()})
    fig, ax = plt.subplots(figsize=(9, 3.4))
    por_hora.plot(ax=ax, lw=1.3)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("Hora local"); ax.set_ylabel("correlación con el precio")
    ax.set_xticks(range(0, 24, 2))
    ax.set_title("La relación de cada driver con el precio cambia según la hora")
    ax.legend(frameon=False, fontsize=7, ncol=2)
    amp = (por_hora.max() - por_hora.min()).round(3)
    cifra("Amplitud de la correlación entre horas", amp.to_dict())
    guardar(fig, 12, "correlacion_por_hora",
            "Correlación driver-precio hora a hora. Una amplitud grande significa que la "
            "correlación única del ranking global no describe ninguna hora concreta: hace "
            "falta interacción con la hora del día.", fmt)


def fig13(df, C, fmt):
    DIA = df.groupby("dia").mean(numeric_only=True)
    DIA.index = pd.to_datetime(DIA.index)
    cand = {k: v for k, v in {
        "gas": C["gas"], "temperatura": C["t2m"], "eólica real": C["eolica"],
        "solar FV real": C["solar"], "hidráulica": C["hidro"],
    }.items() if v and v in DIA.columns}
    lags = range(-7, 8)
    X = pd.DataFrame({k: pd.Series({l: DIA[C["precio"]].corr(DIA[v].shift(l)) for l in lags})
                      for k, v in cand.items()})
    fig, ax = plt.subplots(figsize=(9, 3.2))
    X.plot(ax=ax, marker="o", ms=3, lw=1.2)
    ax.axvline(0, color="black", lw=0.8); ax.axhline(0, color="grey", lw=0.6)
    ax.set_xlabel("desfase en días (positivo: el driver adelanta al precio)")
    ax.set_ylabel("correlación")
    ax.set_title("Correlación cruzada con desfase: qué lag aprovecha mejor cada driver")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    optimo = X.apply(lambda s: s.abs().idxmax()).to_dict()
    cifra("Desfase óptimo en días", optimo)
    guardar(fig, 13, "correlacion_desfase",
            "Correlación del precio con cada driver desplazado en el tiempo. El desfase que "
            "maximiza la asociación es el lag que entra como feature, y permite aprovechar "
            "variables cuya versión contemporánea tiene fuga.", fmt)


# ---------------------------------------------------------------------------
# BLOQUE 4 · Calidad de las entradas
# ---------------------------------------------------------------------------

PARES_PREV = [("demanda", "fc_demanda", "demanda"),
              ("eólica", "fc_eolica", "eolica"),
              ("solar FV", "fc_solar", "solar")]


def _errores_prev(df, C):
    out = {}
    for etq, kp, kr in PARES_PREV:
        cp, cr = C.get(kp), C.get(kr)
        if not (cp and cr):
            continue
        d = df[["hora", "mes_p", cp, cr]].dropna().copy()
        d["err"] = d[cp] - d[cr]
        out[etq] = d
    return out


def fig14(df, C, fmt):
    datos = _errores_prev(df, C)
    if not datos:
        return
    fig, ax = plt.subplots(figsize=(9, 3.2))
    resumen = {}
    for etq, d in datos.items():
        ax.plot(d.groupby("hora")["err"].apply(lambda s: s.abs().mean()),
                marker="o", ms=3, lw=1.3, label=etq)
        resumen[etq] = {"MAE": round(d["err"].abs().mean(), 1),
                        "sesgo": round(d["err"].mean(), 1)}
    ax.set_xlabel("Hora local"); ax.set_ylabel("MAE (MW)"); ax.set_xticks(range(0, 24, 2))
    ax.set_title("Error de las previsiones de REE por hora del día")
    ax.legend(frameon=False)
    cifra("Error de las previsiones (MW)", resumen)
    guardar(fig, 14, "error_previsiones_hora",
            "Error absoluto medio de las previsiones publicadas por REE, que son las únicas "
            "features disponibles a las 12:00 de D. El modelo hereda este error entero: fija "
            "un suelo al que ningún algoritmo puede bajar.", fmt)


def fig15(df, C, fmt):
    datos = _errores_prev(df, C)
    if not datos:
        return
    fig, ax = plt.subplots(figsize=(9, 3.2))
    for etq, d in datos.items():
        m = d.assign(mes=d["mes_p"].dt.month).groupby("mes")["err"].apply(lambda s: s.abs().mean())
        ax.plot(m.index, m.values, marker="o", ms=3, lw=1.3, label=etq)
    ax.set_xlabel("Mes"); ax.set_ylabel("MAE (MW)"); ax.set_xticks(range(1, 13))
    ax.set_title("Error de las previsiones de REE por mes")
    ax.legend(frameon=False)
    guardar(fig, 15, "error_previsiones_mes",
            "Estacionalidad del error de previsión. Los meses de mayor error coinciden con "
            "los de mayor dificultad de predicción del precio: explica de antemano dónde "
            "fallará el modelo.", fmt)


def fig16(df, C, fmt):
    if not all([C["demanda"], C["eolica"], C["solar"]]):
        return
    m = df.groupby("mes_p").agg(dem=(C["demanda"], "sum"), eol=(C["eolica"], "sum"),
                                sol=(C["solar"], "sum"))
    m["% eólica"] = m["eol"] / m["dem"] * 100
    m["% solar"] = m["sol"] / m["dem"] * 100
    m["% eólica+solar"] = m["% eólica"] + m["% solar"]

    fig, ax = plt.subplots(figsize=(9, 3.2))
    for c, col in [("% eólica", "#2ca02c"), ("% solar", "#f5a623"),
                   ("% eólica+solar", "#1f4e79")]:
        ax.plot(m.index.to_timestamp(), m[c], label=c, lw=1.3, color=col)
    ax.set_ylabel("% de la demanda peninsular"); ax.set_xlabel("")
    ax.set_title("Cobertura de la demanda por eólica y fotovoltaica")
    ax.legend(frameon=False, ncol=3)
    anual = m.groupby(m.index.year)["% solar"].mean().round(1)
    cifra("% medio de la demanda cubierto por solar", anual.to_dict())
    guardar(fig, 16, "cobertura_renovable",
            f"Fracción de la demanda cubierta por renovable variable. La solar pasa del "
            f"{anual.iloc[0]:.1f}% al {anual.iloc[-1]:.1f}%: es la causa física del cambio de "
            f"estructura horaria del precio.", fmt)


# ---------------------------------------------------------------------------
# BLOQUE 5 · Decisiones de datos
# ---------------------------------------------------------------------------

def fig17(df, C, fmt):
    if not C["autocons"]:
        return
    por_mes = df.groupby("mes_p")[C["autocons"]].mean()
    fig, ax = plt.subplots(figsize=(9, 3.0))
    ax.plot(por_mes.index.to_timestamp(), por_mes.values, marker="o", ms=3,
            lw=1.3, color="#c0392b", label="demanda (ree_load − entsoe_load)")
    if C["solar_ree"] and C["solar"]:
        d = df[["mes_p", C["solar_ree"], C["solar"]]].dropna()
        dif = (d[C["solar_ree"]] - d[C["solar"]]).groupby(d["mes_p"]).mean()
        ax.plot(dif.index.to_timestamp(), dif.values, marker="o", ms=3, lw=1.3,
                alpha=0.8, color="#f5a623", label="solar (ree_gsolar − FV limpia)")
        comun = por_mes.index.intersection(dif.index)
        r = por_mes[comun].corr(dif[comun])
        cifra("Correlación entre los dos lados del balance", round(r, 4))
    ax.axhline(0, color="grey", lw=0.8)
    ax.axvline(pd.Timestamp("2025-12-01"), color="black", ls="--", lw=1,
               label="incorporación del autoconsumo")
    ax.set_ylabel("MW"); ax.set_xlabel("")
    ax.set_title("El autoconsumo entra por los dos lados del balance el mismo mes")
    ax.legend(frameon=False, fontsize=7.5)
    guardar(fig, 17, "autoconsumo_quiebre",
            "Diferencia entre las series de ESIOS y las de ENTSO-E, por demanda y por "
            "generación. Ambas rompen en diciembre de 2025 con la misma magnitud: es el "
            "mismo fenómeno medido dos veces, y descarta usar las series de ESIOS directas.",
            fmt)


def fig18(df, C, fmt):
    if not all([C["gas_ent"], C["ccgt"], C["otherth"]]):
        return
    d = df[["anio", C["gas_ent"], C["ccgt"], C["otherth"]]].dropna()
    d = d.assign(suma=d[C["ccgt"]] + d[C["otherth"]])
    offset = (d.groupby("anio")[C["gas_ent"]].mean() - d.groupby("anio")["suma"].mean()).round(0)

    fig, ax = plt.subplots(figsize=(7, 3.0))
    ax.bar(offset.index.astype(str), offset.values, color="#8c564b")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("MW"); ax.set_xlabel("")
    ax.set_title("Offset anual: gas de ENTSO-E menos la suma de ESIOS (CCGT + cogeneración)")
    for x, v in zip(offset.index.astype(str), offset.values):
        ax.text(x, v, f"{v:.0f}", ha="center",
                va="top" if v < 0 else "bottom", fontsize=7)
    cifra("Offset gas ENTSO-E vs suma ESIOS", offset.to_dict())
    cifra("Correlación gas ENTSO-E vs suma", round(d[C["gas_ent"]].corr(d["suma"]), 5))
    guardar(fig, 18, "offset_gas_fuentes",
            f"Diferencia entre el agregado de gas de ENTSO-E y la suma de ESIOS. Cae de "
            f"{offset.iloc[0]:.0f} a {offset.iloc[-1]:.0f} MW: el desfase no es estable, así "
            f"que no se puede usar una fuente en entrenamiento y otra en test.", fmt)


# ---------------------------------------------------------------------------
# BLOQUE 6 · La vara de referencia
# ---------------------------------------------------------------------------

def fig19(df, C, fmt, K=4):
    d = clases_horarias(df, C["precio"], K)
    filas = []
    for dia, g in d.groupby("dia"):
        if len(g) < 20:
            continue
        filas.append({"dia": dia,
                      "perfecto": g.nlargest(K, C["precio"])[C["precio"]].sum()
                                  - g.nsmallest(K, C["precio"])[C["precio"]].sum()})
    E = pd.DataFrame(filas).set_index("dia")
    E.index = pd.to_datetime(E.index)

    tab = pd.crosstab(d["hora"], d["clase"], normalize="index") * 100
    mejores = tab["cara"].nlargest(K).index.tolist()
    peores = tab["barata"].nlargest(K).index.tolist()
    reglas = []
    for dia, g in d.groupby("dia"):
        c_ = g[g["hora"].isin(peores)]
        v_ = g[g["hora"].isin(mejores)]
        if len(c_) < K or len(v_) < K:
            continue
        reglas.append({"dia": dia,
                       "regla": v_.head(K)[C["precio"]].sum() - c_.head(K)[C["precio"]].sum()})
    R = pd.DataFrame(reglas).set_index("dia")
    R.index = pd.to_datetime(R.index)
    E = E.join(R, how="inner")

    fig, ax = plt.subplots(figsize=(9, 3.2))
    men = E.resample("MS").mean()
    ax.plot(men.index, men["perfecto"], lw=1.4, color="#1f4e79", label="previsión perfecta")
    ax.plot(men.index, men["regla"], lw=1.4, color="#f5a623", label="regla horaria fija")
    ax.fill_between(men.index, men["regla"], men["perfecto"], alpha=0.15, color="#1f4e79")
    ax.set_ylabel("EUR/día por MWh de batería"); ax.set_xlabel("")
    ax.set_title(f"Ingreso de arbitraje con {K} horas de carga y descarga")
    ax.legend(frameon=False, ncol=2)

    pct = E["regla"].sum() / E["perfecto"].sum() * 100
    cifra("Ingreso diario con previsión perfecta", f"{E['perfecto'].mean():.2f} EUR/MWh")
    cifra("Ingreso diario con regla horaria fija", f"{E['regla'].mean():.2f} EUR/MWh")
    cifra("% del techo que captura la regla fija", f"{pct:.1f}%")
    guardar(fig, 19, "arbitraje_techo_vs_regla",
            f"Ingreso de arbitraje diario. Una regla fija —descargar siempre a las mismas "
            f"horas— captura el {pct:.1f}% del máximo teórico. Ese margen restante es todo lo "
            f"que un modelo puede aportar, y la referencia contra la que debe evaluarse.", fmt)


def fig20(df, C, fmt, K=4):
    d = clases_horarias(df, C["precio"], K)
    tab = pd.crosstab(d["hora"], d["clase"], normalize="index") * 100
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(tab.index, tab["cara"], marker="o", ms=3.5, lw=1.6, color="#c0392b", label="cara")
    ax.plot(tab.index, tab["barata"], marker="o", ms=3.5, lw=1.6, color="#2b7bba", label="barata")
    ax.axhline(100 * K / 24, color="grey", ls="--", lw=1, label="tasa base (azar)")
    ax.set_xlabel("Hora local"); ax.set_ylabel("% de días en esa clase")
    ax.set_xticks(range(0, 24, 2))
    ax.set_title("Probabilidad de que una hora sea de las más caras o de las más baratas del día")
    ax.legend(frameon=False, ncol=3)

    mejores = tab["cara"].nlargest(K).index.tolist()
    peores = tab["barata"].nlargest(K).index.tolist()
    acierto = d[d["hora"].isin(mejores)]["es_cara"].mean() * 100
    base = d["es_cara"].mean() * 100
    cifra("Horas de descarga de la regla fija", sorted(mejores))
    cifra("Horas de carga de la regla fija", sorted(peores))
    cifra("Acierto de la regla fija", f"{acierto:.1f}% frente al {base:.1f}% del azar")
    guardar(fig, 20, "probabilidad_hora_cara",
            f"Distribución horaria de las horas extremas. Descargar en {sorted(mejores)} "
            f"acierta el {acierto:.1f}% de las veces frente al {base:.1f}% del azar "
            f"(×{acierto/base:.2f}).", fmt)


# ---------------------------------------------------------------------------
# Tabla de decisiones y salidas de texto
# ---------------------------------------------------------------------------

DECISIONES = [
    ("spot_es_esios", "TARGET", "objetivo", "Las tres fuentes ES son idénticas (r=1,0000). Se elige por tener menos nulos."),
    ("spot_es_omie / spot_es_entsoe", "descartada", "—", "Idénticas a la elegida: aportarían copias."),
    ("spot_* (11 zonas europeas)", "CON FUGA", "sólo el precio de D", "Casación SDAC simultánea a las 12:00."),
    ("lags del precio D-1 y D-7", "SIN FUGA", "uso directo", "r=0,911 y 0,818: las features más fuertes disponibles."),
    ("forecast_demanda_mercado_prev_mw", "SIN FUGA", "uso directo", "MAE 257 MW, prácticamente insesgada."),
    ("forecast_gen_wind_prev_mw", "SIN FUGA", "corregir sesgo", "MAE 772 MW y sesgo +128 MW: sobreestima."),
    ("forecast_gen_solar_pv_prev_mw", "SIN FUGA", "corregir sesgo", "MAE 547 MW y sesgo +198 MW. Mejor separador de horas caras."),
    ("calc_demanda_residual_prev_mw", "SIN FUGA", "derivar", "Demanda prevista menos renovable prevista: sin fuga por construcción."),
    ("commodities_gas_ttf_m1", "DESFASE D-2", "lag de 2 días", "Cierra ~17:30, tras el cierre eléctrico."),
    ("commodities_co2_eua_dec", "DESFASE D-2", "lag de 2 días", "Ídem."),
    ("era5_*", "CON FUGA", "sólo con lag o ablación", "Reanálisis. Sin ECMWF histórico, la meteo da cota superior optimista."),
    ("era5_wind_gust10_mean / tp_mean", "CON FUGA", "no interpolar linealmente", "Variables de pico: interpolar subestima los extremos."),
    ("generation / load_inter", "CON FUGA", "lag D-1 y D-7", "Consecuencia del precio, no predictor."),
    ("entsoe_pumping_gen_mw", "CON FUGA", "fillna(0) antes de agregar", "NaN significa 'no hubo turbinación', no 'se desconoce'."),
    ("load_inter_entsoe_load", "CON FUGA", "reparar 9 ceros espurios", "Fallo de ingesta documentado en pipeline_log."),
    ("esios_gen_ree_gsolar_mw", "descartada", "usar calc_solar_fv_mw", "Autoconsumo incorporado desde dic-2025 (D-03)."),
    ("load_inter_ree_load", "descartada", "usar entsoe_load", "Mismo motivo (D-03)."),
    ("esios_gen_ree_ghidro_mw", "descartada", "usar las de ENTSO-E", "Mezcla generación y bombeo: 8.831 horas negativas."),
    ("entsoe_gas_mw", "descartada", "usar ESIOS separado", "Mezcla CCGT y cogeneración; offset no estable (D-02)."),
    ("cap_inst_* constantes (6)", "descartada", "—", "Varianza cero en seis años (D-04)."),
    ("cap_disp_*", "CONDICIONAL", "no usar hasta tener _fc", "D-01: el valor guardado ya conoce el día."),
    ("esios_pdbc_gen", "CON FUGA", "sólo con lag, si aporta", "Contemporáneo es circular."),
]


def escribir_salidas():
    SALIDA.mkdir(parents=True, exist_ok=True)
    docs = RAIZ / "docs"

    with (SALIDA / "pies_de_figura.md").open("w", encoding="utf-8") as f:
        f.write("# Pies de figura\n\n")
        f.write("*Generado por `eda/figuras_memoria.py`. No editar a mano: se regenera.*\n\n")
        for n, fichero, pie in sorted(PIES):
            f.write(f"**Figura {n}** — `{fichero}`\n\n{pie}\n\n")

    dec = pd.DataFrame(DECISIONES,
                       columns=["columna", "veredicto", "tratamiento", "motivo"])
    dec.to_csv(docs / "decisiones_features.csv", index=False, encoding="utf-8")

    with (docs / "cifras_clave.md").open("w", encoding="utf-8") as f:
        f.write("# Cifras clave del EDA\n\n")
        f.write("*Generado por `eda/figuras_memoria.py`. Son los números que van en el texto*\n")
        f.write("*de la memoria, no en las figuras.*\n\n")
        for concepto, valor in CIFRAS:
            f.write(f"- **{concepto}**: {valor}\n")

    print(f"\n  {SALIDA / 'pies_de_figura.md'}")
    print(f"  {docs / 'decisiones_features.csv'}  ({len(dec)} columnas decididas)")
    print(f"  {docs / 'cifras_clave.md'}  ({len(CIFRAS)} cifras)")


# ---------------------------------------------------------------------------

FIGURAS = {1: fig01, 2: fig02, 3: fig03, 4: fig04, 5: fig05, 6: fig06, 7: fig07,
           8: fig08, 9: fig09, 10: fig10, 11: fig11, 12: fig12, 13: fig13,
           14: fig14, 15: fig15, 16: fig16, 17: fig17, 18: fig18, 19: fig19, 20: fig20}


def main():
    ap = argparse.ArgumentParser(description="Genera las figuras de la memoria.")
    ap.add_argument("--solo", nargs="*", type=int, help="números de figura a generar")
    ap.add_argument("--formato", default="png", choices=["png", "pdf", "svg"])
    args = ap.parse_args()

    estilo()
    df = cargar()
    C = columnas(df)
    print()

    pedidas = args.solo or sorted(FIGURAS)
    for n in pedidas:
        if n not in FIGURAS:
            print(f"  fig{n:02d} no existe")
            continue
        try:
            FIGURAS[n](df, C, args.formato)
        except Exception as e:
            print(f"  fig{n:02d} FALLÓ: {type(e).__name__}: {e}")

    escribir_salidas()
    print(f"\n{len(PIES)} figuras en {SALIDA}")


if __name__ == "__main__":
    main()
