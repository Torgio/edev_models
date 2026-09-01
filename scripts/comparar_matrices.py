"""¿En que se diferencian matriz_nucleo y matriz_produccion DENTRO de train?

POR QUE
El contrato de predecir.py falla por el escalador: el canal 31 (entsoe_load) pasa de
mu 26892.63 a 26896.37. Segun su propio razonamiento eso no deberia pasar -- los
escaladores se ajustan solo sobre train (`Escalador().fit(X_enc[tr])`) y el corte de
train va por fecha fija, asi que anadir dias al final no puede moverlos.

Comparando los CSV, cero de 129 columnas numericas cambian en train. Pero
`preparar_tensores` lee el PARQUET, no el CSV. Si el parquet si cambia, el CSV que hay
en el repo esta desincronizado -- y entonces el leaderboard, que se calculo desde el CSV,
no describe a los modelos que se sirven.

    python scripts/comparar_matrices.py
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
GOLD = REPO / "data" / "gold"


def cargar(nombre, ext):
    f = GOLD / f"matriz_{nombre}.{ext}"
    d = pd.read_parquet(f) if ext == "parquet" else pd.read_csv(f, parse_dates=["ts"])
    return d.assign(ts=pd.to_datetime(d.ts)).set_index("ts").sort_index()


def comparar(a, b, etq):
    tr = a.split.eq("train")
    idx = a.index[tr].intersection(b.index[b.split.eq("train")])
    num = [c for c in a.columns if a[c].dtype.kind in "fi" and c in b.columns]
    dif = {c: float(np.nanmax(np.abs(a.loc[idx, c].values - b.loc[idx, c].values))) for c in num}
    cambian = {c: v for c, v in dif.items() if v > 1e-6}
    print(f"\n{etq}: {len(idx):,} horas de train comunes · "
          f"{len(cambian)} de {len(num)} columnas cambian")
    for c, v in sorted(cambian.items(), key=lambda x: -x[1])[:15]:
        pa, pb = a.loc[idx, c], b.loc[idx, c]
        print(f"    {c:32s} dif max {v:12.4f}   media {pa.mean():11.2f} -> {pb.mean():11.2f}"
              f"   ({100*(np.abs(pa-pb) > 1e-6).mean():5.1f}% de horas)")
    return cambian


def main():
    np_ = cargar("nucleo", "parquet")
    pp_ = cargar("produccion", "parquet")
    print(f"parquet nucleo     {np_.shape}  {np_.index.min():%Y-%m-%d} -> {np_.index.max():%Y-%m-%d}")
    print(f"parquet produccion {pp_.shape}  {pp_.index.min():%Y-%m-%d} -> {pp_.index.max():%Y-%m-%d}")
    comparar(np_, pp_, "PARQUET nucleo vs PARQUET produccion")

    # y de paso: ¿el CSV del repo describe al parquet que se usa para modelar?
    nc = cargar("nucleo", "csv")
    comparar(nc, np_, "CSV nucleo vs PARQUET nucleo (¿estan sincronizados?)")

    print("\nLectura:")
    print("  Si el parquet SI cambia en train y el CSV no, el CSV esta viejo: el leaderboard")
    print("  se calculo sobre datos que no son los que ven los modelos servidos.")
    print("  Si no cambia ninguno de los dos, el escalador se movio por otra razon y hay que")
    print("  mirar como se construye el tensor, no la matriz.")


if __name__ == "__main__":
    main()
