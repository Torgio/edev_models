"""Mete el boosting de nucleo en el `por_semilla.csv` que lee el backfill.

POR QUE HACE FALTA
`scripts/guardar_predicciones.py --backfill` decide que modelos cargar en la tabla
`predictions` leyendo `por_semilla.csv` y quedandose con el mejor de cada `familia`.
Los modelos de ML_Magui no estan en ese fichero, asi que hoy quedarian FUERA de
produccion -- incluido el lightgbm, que es candidato a campeon. Los CSV
`pred_test_{lightgbm,xgboost}__s*.csv` ya existen con el nombre exacto que el script
busca, asi que basta con anadir las filas.

QUE SE RELLENA Y QUE NO
MAE y acierto de hora pico son independientes del simulador de bateria, asi que se
copian del leaderboard comun y son comparables con el resto del fichero. Las dos
columnas de CAPTURA se dejan VACIAS a proposito: las otras 24 filas se calcularon con
un simulador distinto del comun, y meter aqui un numero de otra escala repetiria
exactamente el problema que el leaderboard vino a resolver. La captura de estos
modelos esta en data_temp/leaderboard_*.csv y en la tabla model_metrics, con sus
supuestos al lado.

    python modelos/integrar_por_semilla.py --dry-run
    python modelos/integrar_por_semilla.py
"""
from __future__ import annotations

import argparse, json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "data" / "gold" / "finales_nucleo"
DESTINOS = [GOLD / "por_semilla.csv", REPO / "production" / "models" / "por_semilla.csv"]
SEMILLA_BASE = 42
NUEVAS = ["lightgbm", "xgboost"]


def filas_nuevas() -> pd.DataFrame:
    naive = json.loads((GOLD / "meta.json").read_text(encoding="utf-8"))["naive_val_MAE"]
    lb = {t: pd.read_csv(REPO / "data_temp" / f"leaderboard_{t}.csv").set_index("modelo")
          for t in ("validation", "test")}
    filas = []
    for fam in NUEVAS:
        for s in range(3):
            clave = f"{fam}__s{s}"
            if clave not in lb["validation"].index or clave not in lb["test"].index:
                print(f"  aviso: falta {clave} en el leaderboard, se salta"); continue
            v, t = lb["validation"].loc[clave], lb["test"].loc[clave]
            filas.append({
                "familia": fam,
                "semilla": SEMILLA_BASE + s,
                "parametros": pd.NA,
                "MAE_val": round(float(v["MAE"]), 3),
                "MAE_test": round(float(t["MAE"]), 3),
                "captura_val_%": pd.NA,          # simulador distinto: ver docstring
                "captura_test_%": pd.NA,
                "pico_1h_test_%": round(float(t["pico_1h_%"]), 2),
                "vs_naive_val_%": round(100 * (float(v["MAE"]) / naive - 1), 1),
            })
    return pd.DataFrame(filas)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    nuevas = filas_nuevas()
    print(nuevas.to_string(index=False), "\n")

    for destino in DESTINOS:
        if not destino.exists():
            print(f"  {destino.relative_to(REPO)}: no existe, se salta"); continue
        d = pd.read_csv(destino)
        ya = sorted(set(d.familia) & set(NUEVAS))
        base = d[~d.familia.isin(NUEVAS)]                 # idempotente: reemplaza, no duplica
        salida = pd.concat([base, nuevas.reindex(columns=d.columns)], ignore_index=True)
        accion = "actualiza" if ya else "anade"
        print(f"  {destino.relative_to(REPO)}: {len(d)} -> {len(salida)} filas ({accion} {NUEVAS})")
        if not a.dry_run:
            salida.to_csv(destino, index=False)

    if a.dry_run:
        print("\n--dry-run: no se ha escrito nada")
    else:
        print("\nListo. Ahora el backfill ya ve los seis modelos:")
        print("  python scripts/guardar_predicciones.py --crear-tabla --backfill")
        print("  python scripts/guardar_predicciones.py --resumen")


if __name__ == "__main__":
    main()
