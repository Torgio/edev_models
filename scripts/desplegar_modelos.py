"""Llevar a `production/models` el representante de cada familia.

POR QUE UN SCRIPT Y NO UN COPIAR-PEGAR
El representante de cada familia es el de MEJOR MAE DE VALIDACION, y cambia entre
reentrenamientos: tras arreglar la fuga del encoder, `seq2seq` paso de la semilla 1 a la 2 y
`simplernn` de la 2 a la 1. Copiar por nombre de fichero dejaria el modelo viejo con el
nombre nuevo y nadie se enteraria.

Ademas VACIA la carpeta antes de copiar. Si una familia deja de tener representante -- o se
renombra -- su .keras viejo se quedaria ahi y `predecir.py` lo cargaria tan tranquilo.

    python scripts/desplegar_modelos.py            # muestra que haria
    python scripts/desplegar_modelos.py --hacerlo
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
ORIGEN = REPO / "data" / "gold" / "finales_nucleo"
DESTINO = REPO / "production" / "models"
SEMILLA = 42


def plan():
    d = pd.read_csv(ORIGEN / "por_semilla.csv")
    rep = d.loc[d.groupby("familia").MAE_val.idxmin()].sort_values("MAE_test")
    piezas = []
    for r in rep.itertuples():
        s = int(r.semilla) - SEMILLA
        keras = ORIGEN / f"{r.familia}__s{s}.keras"
        carpeta = ORIGEN / f"{r.familia}__s{s}"
        origen = keras if keras.exists() else (carpeta if carpeta.is_dir() else None)
        piezas.append({"familia": r.familia, "semilla": s, "origen": origen,
                       "pre": ORIGEN / f"{r.familia}.preprocesado.json",
                       "MAE_val": r.MAE_val, "MAE_test": r.MAE_test})
    return rep, piezas


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hacerlo", action="store_true",
                    help="sin esto solo enseña lo que haria")
    a = ap.parse_args()

    rep, piezas = plan()
    faltan = [p["familia"] for p in piezas if p["origen"] is None]
    print(f"\n  {len(piezas)} familias · origen {ORIGEN}")
    print(f"  {'familia':18s} {'sem':>4s} {'MAE_val':>8s} {'MAE_test':>9s}  fichero")
    print("  " + "-" * 74)
    for p in piezas:
        nom = p["origen"].name + ("/" if p["origen"] and p["origen"].is_dir() else "") \
            if p["origen"] else "FALTA"
        print(f"  {p['familia']:18s} s{p['semilla']:<3d} {p['MAE_val']:8.3f} "
              f"{p['MAE_test']:9.3f}  {nom}")
    if faltan:
        raise SystemExit(f"\n  faltan modelos en disco: {faltan}. Reentrena con "
                         f"--guardar-modelos antes de desplegar.")

    if not a.hacerlo:
        print(f"\n  (simulacion) con --hacerlo se vaciaria {DESTINO} y se copiaria esto")
        return

    DESTINO.mkdir(parents=True, exist_ok=True)
    # Vaciar primero: un .keras huerfano de un despliegue anterior seria cargado sin avisar.
    for x in DESTINO.iterdir():
        shutil.rmtree(x) if x.is_dir() else x.unlink()

    n = 0
    for p in piezas:
        o = p["origen"]
        shutil.copytree(o, DESTINO / o.name) if o.is_dir() else shutil.copy2(o, DESTINO)
        shutil.copy2(p["pre"], DESTINO)
        n += 1
    for extra in ("por_semilla.csv", "meta.json"):
        if (ORIGEN / extra).exists():
            shutil.copy2(ORIGEN / extra, DESTINO)

    print(f"\n  desplegadas {n} familias en {DESTINO}")
    print(f"  {sum(1 for _ in DESTINO.iterdir())} entradas · "
          f"{sum(f.stat().st_size for f in DESTINO.rglob('*') if f.is_file())/2**20:.1f} MB")
    print("\n  comprueba con:  python scripts/predecir.py --listar")


if __name__ == "__main__":
    main()
