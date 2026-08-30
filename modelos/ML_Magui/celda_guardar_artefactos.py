# ============================================================================
# GUARDAR ARTEFACTOS + METADATA  --  pegar como ultima celda del notebook
# Reentrena las 6 combinaciones (mismos hiperparametros, mismas semillas) y las
# guarda en disco, comprueba que reproducen las predicciones ya exportadas, y
# escribe un metadata.json por algoritmo.
# Tarda unos minutos. Reutiliza: ALGORITMOS, SEMILLA_BASE, SEMILLAS, X_train,
# y_train, X_resto, feature_cols, df, mask_resto, preds_semilla, pivotear_pred.
# ============================================================================
import json, sys, platform
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost, lightgbm

# raiz del repo, la busque quien la busque desde donde este el notebook
REPO = Path.cwd().resolve()
while not (REPO / "data" / "gold" / "matriz_nucleo.csv").exists() and REPO != REPO.parent:
    REPO = REPO.parent

ART = REPO / "modelos" / "ML_Magui" / "artefactos"
ART.mkdir(parents=True, exist_ok=True)

meta_matriz = json.loads((REPO / "data" / "gold" / "matriz_nucleo.meta.json").read_text(encoding="utf-8"))
TRAIN_DESDE = str(pd.to_datetime(df.loc[mask_train, "fecha_objetivo"]).min().date())
TRAIN_HASTA = str(pd.to_datetime(df.loc[mask_train, "fecha_objetivo"]).max().date())

VERSIONES = {"XGBoost": f"xgboost=={xgboost.__version__}",
             "LightGBM": f"lightgbm=={lightgbm.__version__}"}

rutas, comprobaciones = {}, []

for algo, (cls, kwargs) in ALGORITMOS.items():
    for s in range(SEMILLAS):
        semilla = SEMILLA_BASE + s
        modelo = cls(**kwargs, random_state=semilla)
        modelo.fit(X_train, y_train)

        # formato NATIVO, no pickle: sobrevive a un cambio de version de la libreria
        if algo == "LightGBM":
            destino = ART / f"lightgbm__s{s}.txt"
            modelo.booster_.save_model(str(destino))
        else:
            destino = ART / f"xgboost__s{s}.json"
            modelo.save_model(str(destino))
        rutas.setdefault(algo, []).append(destino.relative_to(REPO).as_posix())

        # ¿reproduce lo que ya se exporto? si no, algo no es determinista y hay que saberlo
        nuevo = pivotear_pred(pd.Series(modelo.predict(X_resto), index=df.loc[mask_resto].index),
                              df.loc[mask_resto])
        viejo = preds_semilla[(algo, semilla)]
        dif = (nuevo - viejo).abs().max().max()
        comprobaciones.append((algo, semilla, dif))
        print(f"{algo:10s} s{semilla} -> {destino.name:24s} | max dif vs exportado: {dif:.2e}")

peor = max(d for _, _, d in comprobaciones)
print(f"\nreproducibilidad: diferencia maxima {peor:.2e} "
      f"{'OK' if peor < 1e-6 else '<-- REVISAR: no es determinista'}")

# ------------------------------------------------------------------ metadata
for algo, prefijo in [("XGBoost", "xgboost"), ("LightGBM", "lightgbm")]:
    meta = {
        "modelo_id": prefijo,
        "familia": "boosting",
        "autor": "Magdalena",
        "matriz": "nucleo",
        "hash_matriz": meta_matriz.get("hash"),
        "libreria": VERSIONES[algo],
        "python": platform.python_version(),
        "entrenado_desde": TRAIN_DESDE,
        "entrenado_hasta": TRAIN_HASTA,
        "incluye_crisis_2021_2022": TRAIN_DESDE <= "2021-01-01",
        "semillas": [SEMILLA_BASE + s for s in range(SEMILLAS)],
        "hiperparametros": {k: (v if not isinstance(v, np.generic) else v.item())
                            for k, v in ALGORITMOS[algo][1].items()},
        "n_features": len(feature_cols),
        "features": list(feature_cols),
        "features_dudosas": [],
        "auditoria_frontera": "pendiente de correr scripts/auditoria_frontera.py sobre esta lista",
        "artefactos": rutas[algo],
        "ficheros_prediccion": [f"data/gold/finales_nucleo/pred_{t}_{prefijo}__s{s}.csv"
                                for t in ("val", "test") for s in range(SEMILLAS)],
        "formato_prediccion": "ancho: fecha_objetivo x h00..h23 (como las redes)",
        "ventana_val": "2025-01-02 -> 2026-01-01",
        "ventana_test": "2026-01-02 -> 2026-07-31",
        "notas": "Representante por MAE de validacion, nunca mirando test.",
    }
    destino = ART.parent / f"metadata_{prefijo}.json"
    destino.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"escrito {destino.relative_to(REPO)}")

# candidatas a revisar en la auditoria de frontera, para no dejarlo a ojo
sospechosas = [c for c in feature_cols if c.endswith("_D") or "_prev" in c]
print(f"\n{len(sospechosas)} columnas terminan en '_D' o llevan '_prev': "
      f"son las que hay que pasar por auditoria_frontera.py antes de dar el modelo por bueno.")
print(sospechosas[:12], "..." if len(sospechosas) > 12 else "")
