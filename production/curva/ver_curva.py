"""Dibujar la curva publicada. Solo lee el artefacto: ni matriz, ni base, ni modelos.

Es la prueba de que la separacion funciona. `generar_curva.py` produce un `.npy` con los
escenarios y un indice, y a partir de ahi cualquiera puede mirar la curva -- o optimizar una
bateria sobre ella -- sin tener montada la cadena de datos.

    python production/curva/ver_curva.py
    python production/curva/ver_curva.py --mes 2033-07 --salida figuras/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(Path(__file__).resolve().parent))

ROJO, AZUL, GRIS = "#c0392b", "#2874a6", "#7f8c8d"


def cargar(salida: Path | None = None):
    from generar_curva import leer, SALIDA
    sims, idx, meta = leer(salida or SALIDA)
    dias = pd.DatetimeIndex(pd.to_datetime(idx.dia).unique())
    return sims.reshape(len(sims), len(dias), 24), dias, meta


def dibujar(px, dias, meta, mes=None, salida: Path | None = None, mostrar=True):
    """Cuatro vistas del mismo objeto. Devuelve las rutas de lo que haya guardado."""
    anos = dias.year.to_numpy()
    p10, p50, p90 = np.percentile(px, [10, 50, 90], axis=0)
    hechos = []

    def _fin(fig, nombre):
        if salida:
            salida.mkdir(parents=True, exist_ok=True)
            f = salida / f"{nombre}.png"
            fig.savefig(f, dpi=110, bbox_inches="tight")
            hechos.append(f)
        if mostrar and not salida:
            plt.show()
        plt.close(fig)

    # ── 1 · el mapa: 24 horas en el eje x, un año por fila ────────────────────
    # Es la vista mas informativa de las cuatro: se lee de arriba abajo como el valle de
    # mediodia se hunde hasta el cero mientras el pico de la tarde aguanta.
    mapa = pd.DataFrame(p50.mean(axis=0) if False else None) if False else None
    tabla = pd.DataFrame({"a": np.repeat(anos, 24), "h": np.tile(np.arange(24), len(dias)),
                          "p": p50.ravel()})
    mapa = tabla.pivot_table(index="a", columns="h", values="p")
    completos = tabla.groupby("a").size() >= 365 * 24
    mapa = mapa[completos.reindex(mapa.index, fill_value=False)]

    fig, ax = plt.subplots(1, 2, figsize=(14, 6.5), gridspec_kw={"width_ratios": [1.25, 1]})
    norm = TwoSlopeNorm(vmin=min(mapa.values.min(), -1), vcenter=0, vmax=mapa.values.max())
    im = ax[0].pcolormesh(mapa.columns, mapa.index, mapa.values, cmap="RdYlBu_r",
                          norm=norm, shading="nearest")
    ax[0].set_xticks(range(0, 24, 2)); ax[0].set_xlabel("hora del día")
    ax[0].set_ylabel("año"); ax[0].invert_yaxis(); ax[0].grid(False)
    ax[0].set_title(f"Curva horaria P50 · {mapa.index.min()}-{mapa.index.max()}", fontsize=12)
    plt.colorbar(im, ax=ax[0], label="€/MWh")

    cols = plt.cm.plasma(np.linspace(.08, .92, len(mapa)))
    for (a_, fila), col in zip(mapa.iterrows(), cols):
        ax[1].plot(fila.index, fila.values, lw=1.6, color=col)
    ax[1].axhline(0, color=GRIS, lw=.9)
    ax[1].set_xticks(range(0, 24, 3)); ax[1].set_xlabel("hora del día")
    ax[1].set_ylabel("€/MWh"); ax[1].grid(alpha=.3)
    ax[1].set_title(f"Los {len(mapa)} perfiles, de claro a oscuro", fontsize=12)
    plt.tight_layout(); _fin(fig, "curva_mapa")

    # ── 2 · la serie completa, media diaria, con banda ───────────────────────
    fig, ax = plt.subplots(figsize=(14, 5))
    d10, d50, d90 = p10.mean(axis=1), p50.mean(axis=1), p90.mean(axis=1)
    ax.fill_between(dias, d10, d90, alpha=.16, color=ROJO, lw=0, label="P10 - P90")
    ax.plot(dias, d50, lw=.3, color=ROJO, alpha=.6)
    ax.plot(dias, pd.Series(d50).rolling(90, center=True).mean(), lw=2.2, color=ROJO,
            label="P50 (media móvil 90 d)")
    ax.axhline(0, color=GRIS, lw=.9)
    ax.set_ylabel("€/MWh"); ax.legend()
    ax.set_title(f"{meta['desde']} -> {meta['hasta']} · media diaria · "
                 f"{meta['escenarios']} escenarios", fontsize=13)
    ax.grid(alpha=.3); plt.tight_layout(); _fin(fig, "curva_serie")

    # ── 3 · la banda por hora del dia, consolidada ───────────────────────────
    fig, ax = plt.subplots(figsize=(11, 5))
    h10 = p10.reshape(-1, 24).mean(axis=0)
    h50 = p50.reshape(-1, 24).mean(axis=0)
    h90 = p90.reshape(-1, 24).mean(axis=0)
    ax.fill_between(range(24), h10, h90, alpha=.22, color=ROJO, label="P10 - P90")
    ax.plot(range(24), h50, "o-", color=ROJO, lw=2.4, ms=5, label="P50")
    ax.axhline(0, color=GRIS, lw=.9)
    ax.set_xticks(range(24)); ax.set_xlabel("hora del día"); ax.set_ylabel("€/MWh")
    ax.set_title("Curva horaria consolidada de todo el periodo", fontsize=13)
    ax.legend(); ax.grid(alpha=.3); plt.tight_layout(); _fin(fig, "curva_horaria")

    # ── 4 · un mes concreto, hora a hora ─────────────────────────────────────
    if mes is None:
        mes = f"{(dias[0].year + dias[-1].year) // 2}-07"
    m = (dias >= pd.Timestamp(mes)) & (dias < pd.Timestamp(mes) + pd.offsets.MonthBegin(1))
    if m.any():
        ts = (np.repeat(dias[m].to_numpy(), 24)
              + np.tile(np.arange(24) * np.timedelta64(1, "h"), int(m.sum())))
        fig, ax = plt.subplots(figsize=(14, 5))
        for k in range(min(len(px), 20)):
            ax.plot(ts, px[k][m].ravel(), lw=.4, color=GRIS, alpha=.35)
        ax.fill_between(ts, p10[m].ravel(), p90[m].ravel(), alpha=.2, color=ROJO, lw=0,
                        label="P10 - P90")
        ax.plot(ts, p50[m].ravel(), color=ROJO, lw=1.6, label="P50")
        ax.axhline(0, color=GRIS, lw=.9)
        ax.set_ylabel("€/MWh"); ax.legend()
        ax.set_title(f"Detalle horario · {pd.Timestamp(mes):%B de %Y} · "
                     f"cada línea gris es un escenario", fontsize=13)
        ax.grid(alpha=.3); plt.tight_layout(); _fin(fig, "curva_mes")
    return hechos


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mes", help="AAAA-MM para el detalle horario")
    ap.add_argument("--salida", help="carpeta donde guardar los PNG")
    ap.add_argument("--artefacto", help="carpeta de la curva publicada")
    a = ap.parse_args()

    px, dias, meta = cargar(Path(a.artefacto) if a.artefacto else None)
    print(f"\n  curva del {meta['generado'][:16]} · matriz {meta.get('matriz', '?')} "
          f"hash {meta.get('matriz_hash', '?')}")
    print(f"  {meta['escenarios']} escenarios · {meta['desde']} -> {meta['hasta']}")
    print(f"  media {px.mean():.2f} EUR/MWh · horas <= 0: {(px <= 0).mean():.1%}")

    anos = dias.year.to_numpy()
    print(f"\n  {'año':>5s} {'P10':>8s} {'P50':>8s} {'P90':>8s} {'h<=0':>7s}")
    print("  " + "-" * 42)
    for y in np.unique(anos):
        m = anos == y
        if m.sum() < 365:
            continue
        q = np.percentile(px[:, m, :], [10, 50, 90])
        print(f"  {y:5d} {q[0]:8.1f} {q[1]:8.1f} {q[2]:8.1f} "
              f"{(px[:, m, :] <= 0).mean()*100:6.1f}%")

    if a.salida:
        matplotlib.use("Agg")
    hechos = dibujar(px, dias, meta, a.mes,
                     Path(a.salida) if a.salida else None)
    for f in hechos:
        print(f"  {f}")


if __name__ == "__main__":
    main()
