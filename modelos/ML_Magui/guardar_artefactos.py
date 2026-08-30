"""Entrena, guarda y documenta los modelos de boosting sobre la matriz nucleo.

POR QUE ES UN SCRIPT Y NO UNA CELDA
Una celda depende del estado del kernel: si se reinicia, o si no se han ejecutado
todas las celdas anteriores, revienta con NameError sobre variables que "estaban
ahi hace un rato". Esto se reconstruye entero desde la matriz, asi que se puede
correr desde cero cuantas veces haga falta y siempre da lo mismo.

    python modelos/ML_Magui/guardar_artefactos.py

Deja en modelos/ML_Magui/:
    artefactos/{xgboost,lightgbm}__s{0,1,2}.{json,txt}   modelos servibles
    metadata_{xgboost,lightgbm}.json                     ficha de cada algoritmo
y (re)escribe las predicciones en data/gold/finales_nucleo/.
"""
from __future__ import annotations

import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost
import lightgbm
from xgboost import XGBRegressor
from lightgbm import LGBMRegressor

# ------------------------------------------------------------------ constantes
SEMILLA_BASE, SEMILLAS = 42, 3
TARGET, CONTROL = "target_price", ["fecha_pred", "fecha_objetivo", "ts", "split", "hora"]
TARGET_COLS = [f"price_h{h:02d}" for h in range(24)]

ALGORITMOS = {
    "xgboost": (XGBRegressor, dict(n_estimators=800, learning_rate=0.03, max_depth=6,
                                   subsample=0.8, colsample_bytree=0.8, n_jobs=-1)),
    "lightgbm": (LGBMRegressor, dict(n_estimators=1000, learning_rate=0.03, num_leaves=63,
                                     subsample=0.8, colsample_bytree=0.8, verbose=-1)),
}

REPO = Path(__file__).resolve().parents[2]
GOLD = REPO / "data" / "gold"
FINALES = GOLD / "finales_nucleo"
AQUI = REPO / "modelos" / "ML_Magui"
ART = AQUI / "artefactos"


def pivotear(pred, df_resto):
    tmp = df_resto[["fecha_objetivo", "hora"]].copy()
    tmp["pred"] = np.asarray(pred)
    w = tmp.pivot_table(index="fecha_objetivo", columns="hora", values="pred")
    w.columns = [f"price_h{int(h):02d}" for h in w.columns]
    return w.reindex(columns=TARGET_COLS)


def main():
    ART.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(GOLD / "matriz_nucleo.csv",
                     parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
    meta_matriz = json.loads((GOLD / "matriz_nucleo.meta.json").read_text(encoding="utf-8"))

    feature_cols = [c for c in df.columns if c not in CONTROL + [TARGET]]
    es_train = df["split"] == "train"
    es_resto = df["split"].isin(["validation", "test"])
    X_train, y_train = df.loc[es_train, feature_cols], df.loc[es_train, TARGET]
    resto = df.loc[es_resto]
    X_resto = resto[feature_cols]

    # el tramo de cada dia lo decide la columna split de la matriz, no un corte a mano
    tramo_dia = resto.groupby("fecha_objetivo")["split"].first()
    dias = {"val": tramo_dia.index[tramo_dia == "validation"],
            "test": tramo_dia.index[tramo_dia == "test"]}

    print(f"matriz {meta_matriz.get('hash')} · {len(feature_cols)} features · "
          f"train {es_train.sum():,} filas · val {len(dias['val'])} dias · test {len(dias['test'])} dias\n")

    train_desde = str(df.loc[es_train, "fecha_objetivo"].min().date())
    train_hasta = str(df.loc[es_train, "fecha_objetivo"].max().date())
    versiones = {"xgboost": f"xgboost=={xgboost.__version__}",
                 "lightgbm": f"lightgbm=={lightgbm.__version__}"}

    for algo, (cls, kwargs) in ALGORITMOS.items():
        rutas = []
        for s in range(SEMILLAS):
            semilla = SEMILLA_BASE + s
            modelo = cls(**kwargs, random_state=semilla).fit(X_train, y_train)

            # formato NATIVO, no pickle: sobrevive a un cambio de version de la libreria
            if algo == "lightgbm":
                destino = ART / f"lightgbm__s{s}.txt"
                modelo.booster_.save_model(str(destino))
            else:
                destino = ART / f"xgboost__s{s}.json"
                modelo.save_model(str(destino))
            rutas.append(destino.relative_to(REPO).as_posix())

            ancho = pivotear(modelo.predict(X_resto), resto)
            ancho.columns = [c.replace("price_", "") for c in ancho.columns]

            aviso = ""
            for t, idx in dias.items():
                fichero = FINALES / f"pred_{t}_{algo}__s{s}.csv"
                trozo = ancho.loc[ancho.index.intersection(idx)].sort_index()
                if fichero.exists():                       # ¿reproduce lo ya exportado?
                    previo = pd.read_csv(fichero, index_col=0, parse_dates=True)
                    comun = trozo.index.intersection(previo.index)
                    d = (trozo.loc[comun].values - previo.loc[comun].values)
                    aviso += f"  dif {t} {np.abs(d).max():.2e}"
                trozo.to_csv(fichero)
            print(f"{algo:9s} s{s} -> {destino.name:22s} {len(trozo)} dias test{aviso}")

        meta = {
            "modelo_id": algo,
            "familia": "boosting",
            "autor": "Magdalena",
            "matriz": "nucleo",
            "hash_matriz": meta_matriz.get("hash"),
            "libreria": versiones[algo],
            "python": platform.python_version(),
            "entrenado_desde": train_desde,
            "entrenado_hasta": train_hasta,
            "incluye_crisis_2021_2022": train_desde <= "2021-01-01",
            "semillas": [SEMILLA_BASE + s for s in range(SEMILLAS)],
            "hiperparametros": ALGORITMOS[algo][1],
            "n_features": len(feature_cols),
            "features": feature_cols,
            "features_dudosas": [],
            "auditoria_frontera": "pendiente: correr scripts/auditoria_frontera.py sobre esta lista",
            "artefactos": rutas,
            "formato_prediccion": "ancho: fecha_objetivo x h00..h23 (como las redes)",
            "ventana_val": f"{dias['val'].min():%Y-%m-%d} -> {dias['val'].max():%Y-%m-%d}",
            "ventana_test": f"{dias['test'].min():%Y-%m-%d} -> {dias['test'].max():%Y-%m-%d}",
            "notas": "Representante por MAE de validacion, nunca mirando test.",
        }
        (AQUI / f"metadata_{algo}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"          metadata_{algo}.json escrito\n")

    sosp = [c for c in feature_cols if c.endswith("_D") or "_prev" in c]
    print(f"{len(sosp)} columnas terminan en '_D' o llevan '_prev'. Son las que hay que pasar "
          f"por scripts/auditoria_frontera.py antes de dar el modelo por bueno:")
    print("  " + ", ".join(sosp[:10]) + (" ..." if len(sosp) > 10 else ""))


if __name__ == "__main__":
    main()
