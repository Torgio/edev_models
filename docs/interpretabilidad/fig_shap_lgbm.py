"""Figura de importancia SHAP del LightGBM global en test (dos paneles: grupos y variables).

Lee shap_lgbm_variables.csv y shap_lgbm_grupos.csv (los escribe shap_lgbm.py) y guarda
fig_shap_lgbm.png a 300 ppp, 16 cm de ancho.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path(__file__).resolve().parent
K = 12
CM = 1 / 2.54

# Paleta categorica sobria, orden fijo por grupo (no por rango): la identidad del bloque no
# cambia de color si cambia su posicion. Los grupos sin color propio van en gris.
COLOR = {
    "Precio español (D, D-1, D-6)": "#2a78d6",
    "Precios europeos D y diferenciales": "#eb6834",
    "Gas MIBGAS": "#1baf7a",
    "Calendario y hora": "#eda100",
    "Programas PDBC/PBF de D": "#4a3aa7",
    "Generación, demanda y flujos reales D-1/D-6": "#e87ba4",
    "Previsiones REE (demanda, eólica, solar)": "#008300",
    "Meteorología prevista": "#9a9994",
    "Capacidades (disponible e instalada)": "#9a9994",
    "Otros": "#9a9994",
}
CORTO = {
    "Precio español (D, D-1, D-6)": "Precio español D, D-1, D-6",
    "Precios europeos D y diferenciales": "Precios europeos D y diferenciales",
    "Previsiones REE (demanda, eólica, solar)": "Previsiones REE",
    "Meteorología prevista": "Meteorología prevista",
    "Gas MIBGAS": "Gas MIBGAS",
    "Calendario y hora": "Calendario y hora",
    "Programas PDBC/PBF de D": "Programas PDBC/PBF de D",
    "Generación, demanda y flujos reales D-1/D-6": "Generación y flujos reales D-1/D-6",
    "Capacidades (disponible e instalada)": "Capacidades",
    "Otros": "Otros",
}
TINTA, TINTA2, REJILLA = "#1f1f1e", "#52514e", "#e4e3df"


def main():
    v = pd.read_csv(OUT / "shap_lgbm_variables.csv", index_col=0)
    g = pd.read_csv(OUT / "shap_lgbm_grupos.csv", index_col=0)

    plt.rcParams.update({"font.size": 7, "font.family": "DejaVu Sans", "axes.edgecolor": TINTA2,
                         "axes.labelcolor": TINTA, "xtick.color": TINTA2, "ytick.color": TINTA,
                         "axes.linewidth": 0.6})
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(16 * CM, 7.4 * CM),
                                 gridspec_kw={"width_ratios": [1, 1.12], "wspace": 0.95})

    # Panel a: proporcion de |SHAP| por grupo
    gg = g.sort_values("proporcion_test")
    y = range(len(gg))
    a1.barh(y, gg.proporcion_test * 100, height=0.68, color=[COLOR.get(n, "#9a9994") for n in gg.index],
            edgecolor="white", linewidth=0.6)
    a1.set_yticks(list(y), [CORTO.get(n, n) for n in gg.index])
    for i, val in zip(y, gg.proporcion_test * 100):
        a1.text(val + 0.6, i, f"{val:.1f}".replace(".", ","), va="center", fontsize=6.2, color=TINTA2)
    a1.set_xlabel("Proporción de |SHAP| medio en test (%)")
    a1.set_xlim(0, gg.proporcion_test.max() * 100 * 1.2)
    a1.text(-0.02, 1.03, "a", transform=a1.transAxes, fontweight="bold", fontsize=8, ha="right")

    # Panel b: las K variables con mayor |SHAP| medio
    top = v.nlargest(K, "shap_abs_medio_test").iloc[::-1]
    y = range(len(top))
    a2.barh(y, top.shap_abs_medio_test, height=0.68, color=[COLOR.get(n, "#9a9994") for n in top.grupo],
            edgecolor="white", linewidth=0.6)
    a2.set_yticks(list(y), list(top.index), fontsize=6.4)
    for i, val in zip(y, top.shap_abs_medio_test):
        a2.text(val + 0.15, i, f"{val:.2f}".replace(".", ","), va="center", fontsize=6.2, color=TINTA2)
    a2.set_xlabel("|SHAP| medio en test (€/MWh)")
    a2.set_xlim(0, top.shap_abs_medio_test.max() * 1.18)
    a2.text(-0.02, 1.03, "b", transform=a2.transAxes, fontweight="bold", fontsize=8, ha="right")

    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
        a.grid(axis="x", color=REJILLA, linewidth=0.5)
        a.set_axisbelow(True)
        a.tick_params(axis="y", length=0)
        a.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}".replace(".", ",")))

    fig.savefig(OUT / "fig_shap_lgbm.png", dpi=300, bbox_inches="tight", facecolor="white")
    print("fig_shap_lgbm.png guardada")


if __name__ == "__main__":
    main()
