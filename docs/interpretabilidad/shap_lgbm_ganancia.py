"""Por que la ganancia y el SHAP ordenan distinto (complemento de shap_lgbm.py).

  - Ganancia y numero de divisiones por semilla para pt_entsoe_D y es_esios_D.
  - En entrenamiento: proporcion por grupo de |SHAP| medio, de SHAP^2 medio (pondera las
    contribuciones grandes, como la ganancia cuadratica) y de |SHAP| por anio.

Escribe shap_lgbm_ganancia_semillas.csv y shap_lgbm_ganancia_vs_shap_grupos.csv.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "interpretabilidad"
sys.path.append(str(REPO / "scripts"))
sys.path.append(str(OUT))
import entrenar_finales_v2 as V2  # noqa: E402
from preparar_tensores import TRAIN_END  # noqa: E402
from shap_lgbm import grupo, SAL, MODELO  # noqa: E402


def main():
    filas = []
    for t in sorted(SAL.glob("lgbm_nucleo__s*.txt")):
        b = lgb.Booster(model_file=str(t))
        gain = pd.Series(b.feature_importance("gain"), index=b.feature_name())
        split = pd.Series(b.feature_importance("split"), index=b.feature_name())
        for v in ("pt_entsoe_D", "es_esios_D", "spread_es_pt_D", "es_esios_Dm1"):
            filas.append({"modelo": t.stem, "variable": v, "proporcion_ganancia": gain[v] / gain.sum(),
                          "rango_ganancia": int(gain.rank(ascending=False, method="min")[v]),
                          "divisiones": int(split[v]),
                          "rango_divisiones": int(split.rank(ascending=False, method="min")[v])})
    sem = pd.DataFrame(filas)
    sem.round(5).to_csv(OUT / "shap_lgbm_ganancia_semillas.csv", index=False, encoding="utf-8")
    print(sem.round(4).to_string())

    b = lgb.Booster(model_file=str(SAL / f"{MODELO}.txt"))
    feats = b.feature_name()
    P = V2.cargar_plana("nucleo", V2.columnas_vetadas("nucleo", V2.VETOS_DEFECTO))
    tr = np.asarray(P.index.get_level_values(0) <= pd.Timestamp(TRAIN_END))
    X = P.loc[tr, feats].astype("float64")
    S = b.predict(X, pred_contrib=True)[:, :-1]
    grp = np.array([grupo(c) for c in feats])
    anio = X.index.get_level_values(0).year
    nombres = pd.unique(grp)
    res = pd.DataFrame(index=pd.Index(nombres, name="grupo"))
    tot_abs = np.abs(S).mean(0).sum()
    tot_sq = (S ** 2).mean(0).sum()
    res["proporcion_abs_shap_train"] = [np.abs(S[:, grp == n]).mean(0).sum() / tot_abs for n in nombres]
    res["proporcion_shap2_train"] = [(S[:, grp == n] ** 2).mean(0).sum() / tot_sq for n in nombres]
    for a in sorted(set(anio)):
        m = anio == a
        tot = np.abs(S[m]).mean(0).sum()
        res[f"proporcion_abs_shap_{a}"] = [np.abs(S[m][:, grp == n]).mean(0).sum() / tot for n in nombres]
    gan = V2.importancias_lgbm(SAL)["ganancia_media"].reindex(feats)
    res["proporcion_ganancia_3s"] = [gan[grp == n].sum() / gan.sum() for n in nombres]
    # |SHAP| medio total por anio, en EUR/MWh: cuanto se mueve la prediccion respecto a la base
    res.loc["(total |SHAP| por fila, EUR/MWh)"] = np.nan
    for a in sorted(set(anio)):
        res.loc["(total |SHAP| por fila, EUR/MWh)", f"proporcion_abs_shap_{a}"] = np.abs(S[anio == a]).sum(1).mean()
    res = res.sort_values("proporcion_ganancia_3s", ascending=False)
    res.round(5).to_csv(OUT / "shap_lgbm_ganancia_vs_shap_grupos.csv", encoding="utf-8")
    pd.set_option("display.width", 250)
    print(res.round(3).to_string())


if __name__ == "__main__":
    main()
