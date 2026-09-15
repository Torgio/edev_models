"""Figura: aumento del MAE de test del ensemble al permutar cada grupo de variables.

Lee `permutacion_ensemble.csv` (de `permutacion_ensemble.py`) y dibuja barras horizontales con
la media de las 3 repeticiones y el rango (min-max) como bigote. La variante con el ancla del
residuo tambien permutada va aparte, rayada y bajo una linea fina, como referencia.

Uso:  C:/Users/torgi/anaconda3/python.exe docs/interpretabilidad/fig_permutacion_ensemble.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

AQUI = Path(__file__).resolve().parent
TINTA, TINTA_2, EJE, REJILLA = "#0b0b0b", "#52514e", "#c3c2b7", "#e1e0d9"
BARRA, BARRA_REF = "#2a78d6", "#9ec5f4"

ETIQUETAS = {
    "precio_es": "Precio español (D, D-1, D-6)",
    "precios_europeos": "Precios europeos y spreads (D)",
    "prev_ree": "Previsiones REE (D+1)",
    "meteo_prevista": "Meteorología prevista (D+1)",
    "gas": "Gas (MIBGAS)",
    "calendario": "Calendario (D+1)",
    "programas_D": "Programas de D (PDBC, PBF, bilaterales)",
    "real_Dm1_Dm6": "Generación y flujos reales (D-1, D-6)",
    "capacidades": "Capacidades (instalada y disponible)",
    "precio_es__ancla_permutada": "Precio español con ancla del residuo\ntambién permutada (referencia)",
}


def main():
    d = pd.read_csv(AQUI / "permutacion_ensemble.csv")
    prin = d[d.variante == "principal"].sort_values("aumento_MAE_media", ascending=True)
    ref = d[d.variante != "principal"]
    tabla = pd.concat([ref, prin])            # de abajo arriba: referencia al pie
    ypos = np.arange(len(tabla), dtype=float)
    if len(ref):
        ypos[len(ref):] += 0.6                # hueco para la linea de separacion

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "axes.edgecolor": EJE, "axes.labelcolor": TINTA_2,
                         "xtick.color": TINTA_2, "ytick.color": TINTA})
    fig, ax = plt.subplots(figsize=(16 / 2.54, 0.62 + 0.3 * len(ypos)), dpi=300)
    media = tabla.aumento_MAE_media.to_numpy()
    vmin, vmax = tabla.aumento_MAE_min.to_numpy(), tabla.aumento_MAE_max.to_numpy()
    es_ref = (tabla.variante != "principal").to_numpy()
    # la escala la fijan los grupos principales; la referencia (~2x el mayor) se corta
    xmax = float(prin.aumento_MAE_max.max()) * 1.3
    tope = xmax * 0.55

    def num(v, signo=True):
        if abs(v) < 0.005:
            v = 0.0
        return (f"{v:+.2f}" if signo and v else f"{v:.2f}").replace(".", ",")

    for y0, m, a, b, r, pct in zip(ypos, media, vmin, vmax, es_ref, tabla["aumento_%_media"]):
        pct = 0.0 if abs(pct) < 0.5 else pct
        pct_txt = f"{pct:+.0f} %" if pct else "0 %"
        if not r:
            ax.barh(y0, m, height=0.62, color=BARRA, edgecolor="none", zorder=2)
            ax.errorbar(m, y0, xerr=[[m - a], [b - m]], fmt="none", ecolor=TINTA,
                        elinewidth=0.8, capsize=2.2, capthick=0.8, zorder=4)
            ax.text(max(b, 0) + xmax * 0.012, y0, f"{num(m)}  ({pct_txt})", va="center",
                    ha="left", fontsize=7, color=TINTA_2)
            continue
        ancho = min(m, tope)
        ax.barh(y0, ancho, height=0.62, color=BARRA_REF, edgecolor="none", zorder=2)
        ax.barh(y0, ancho, height=0.62, color="none", edgecolor=BARRA, hatch="////",
                linewidth=0, zorder=3)
        if m > tope:                          # marca de corte
            for dx in (-0.012, 0.012):
                ax.plot([ancho - xmax * 0.02 + xmax * dx, ancho + xmax * dx],
                        [y0 - 0.42, y0 + 0.42], color="white", linewidth=2.2, zorder=5)
        ax.text(ancho + xmax * 0.012, y0,
                f"{num(m)}  ({pct_txt}), barra cortada\nrango {num(a, False)}–{num(b, False)}",
                va="center", ha="left", fontsize=6.5, color=TINTA_2, linespacing=1.3)
    if len(ref):
        ax.axhline(len(ref) - 0.5 + 0.3, color=EJE, linewidth=0.6)
    ax.set_yticks(ypos, [ETIQUETAS.get(g, g) for g in tabla.grupo])
    ax.axvline(0, color=EJE, linewidth=0.8, zorder=1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: f"{v:g}".replace(".", ",")))
    ax.set_xlim(min(0, float(prin.aumento_MAE_min.min()) * 1.2), xmax)
    ax.set_ylim(ypos[0] - 0.6, ypos[-1] + 0.6)
    ax.set_xlabel("Aumento del MAE de test del ensemble (EUR/MWh)")
    ax.grid(axis="x", color=REJILLA, linewidth=0.5, zorder=0)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    fig.savefig(AQUI / "fig_permutacion_ensemble.png", dpi=300, facecolor="white")
    print("guardada", AQUI / "fig_permutacion_ensemble.png")


if __name__ == "__main__":
    main()
