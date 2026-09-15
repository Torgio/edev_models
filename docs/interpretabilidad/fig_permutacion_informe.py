"""Figura del informe: aumento del MAE de test del ensemble al permutar cada grupo de variables.

Version para el informe de `fig_permutacion_ensemble.py` (cuya salida se conserva como
`fig_permutacion_ensemble_largo.png`). No recalcula nada: lee `permutacion_ensemble.csv` y dibuja
barras horizontales con la media de las 3 repeticiones y el rango (min-max) como bigote.
Diferencias con la version larga: eje en €/MWh, grupo europeo rotulado con el portugues y los
diferenciales, porcentajes con un decimal, capacidades rotuladas "≈ 0" y sin la barra de
referencia del ancla permutada (su cifra va en el pie).

Uso:  C:/Users/torgi/anaconda3/python.exe docs/interpretabilidad/fig_permutacion_informe.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "fig_permutacion_ensemble.png"
TINTA, TINTA_2, EJE, REJILLA = "#0b0b0b", "#52514e", "#c3c2b7", "#e1e0d9"
BARRA = "#2a78d6"

ETIQUETAS = {
    "precio_es": "Precio español (D, D-1, D-6)",
    "precios_europeos": "Precios europeos, portugués y diferenciales (D)",
    "prev_ree": "Previsiones REE (D+1)",
    "meteo_prevista": "Meteorología prevista (D+1)",
    "gas": "Gas (MIBGAS)",
    "calendario": "Calendario (D+1)",
    "programas_D": "Programas de D (PDBC, PBF, bilaterales)",
    "real_Dm1_Dm6": "Generación y flujos reales (D-1, D-6)",
    "capacidades": "Capacidades (instalada y disponible)",
}
SIN_EFECTO = {"capacidades"}          # efecto no medible: se rotula "≈ 0"


def coma(txt):
    return txt.replace(".", ",")


def main():
    d = pd.read_csv(AQUI / "permutacion_ensemble.csv")
    tabla = d[d.variante == "principal"].sort_values("aumento_MAE_media", ascending=True)
    ypos = np.arange(len(tabla), dtype=float)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "axes.edgecolor": EJE, "axes.labelcolor": TINTA_2,
                         "xtick.color": TINTA_2, "ytick.color": TINTA})
    fig, ax = plt.subplots(figsize=(16 / 2.54, 0.55 + 0.3 * len(ypos)), dpi=300)
    xmax = float(tabla.aumento_MAE_max.max()) * 1.42     # sitio para el rotulo de la barra mayor

    for y0, g, m, a, b, pct in zip(ypos, tabla.grupo, tabla.aumento_MAE_media,
                                   tabla.aumento_MAE_min, tabla.aumento_MAE_max,
                                   tabla["aumento_%_media"]):
        ax.barh(y0, m, height=0.62, color=BARRA, edgecolor="none", zorder=2)
        ax.errorbar(m, y0, xerr=[[m - a], [b - m]], fmt="none", ecolor=TINTA,
                    elinewidth=0.8, capsize=2.2, capthick=0.8, zorder=4)
        txt = "≈ 0" if g in SIN_EFECTO else coma(f"{m:+.2f}  ({pct:+.1f} %)")
        ax.text(max(b, 0) + xmax * 0.012, y0, txt, va="center", ha="left",
                fontsize=7, color=TINTA_2)

    ax.set_yticks(ypos, [ETIQUETAS.get(g, g) for g in tabla.grupo])
    ax.axvline(0, color=EJE, linewidth=0.8, zorder=1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: coma(f"{v:g}")))
    ax.set_xlim(min(0, float(tabla.aumento_MAE_min.min()) * 1.2), xmax)
    ax.set_ylim(ypos[0] - 0.6, ypos[-1] + 0.6)
    ax.set_xlabel("Aumento del MAE de test del ensemble (€/MWh)")
    ax.grid(axis="x", color=REJILLA, linewidth=0.5, zorder=0)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    fig.savefig(SALIDA, dpi=300, facecolor="white")
    print("guardada", SALIDA)


if __name__ == "__main__":
    main()
