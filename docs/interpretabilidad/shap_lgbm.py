"""Interpretabilidad del LightGBM global (lgbm_nucleo__s1), miembro de mayor peso del ensemble.

Solo lectura sobre el repositorio: todo lo que escribe va a docs/interpretabilidad.

  1. SHAP exacto (TreeSHAP, booster.predict(pred_contrib=True)) sobre filas horarias de test,
     validacion y entrenamiento.
  2. Agrupacion de variables por bloque (regla de nombre explicita, gana la primera que case).
  3. Contraste con la ganancia media de las tres semillas (V2.importancias_lgbm).
  4. Precio portugues frente a espanol: correlacion, coincidencia, huecos e importancia por
     permutacion por dias sobre el MAE de test.
  5. Contraste con el ranking de Spearman del analisis exploratorio.

Uso:  python docs/interpretabilidad/shap_lgbm.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "scripts"))
import entrenar_finales_v2 as V2  # noqa: E402
from preparar_tensores import TRAIN_END, VAL_END  # noqa: E402

OUT = REPO / "docs" / "interpretabilidad"
SAL = REPO / "data" / "gold" / "finales_v2_nucleo"
MODELO = "lgbm_nucleo__s1"
N_PERM = 30
SEMILLA = 2026

# ── Grupos: regla por nombre, gana la primera que case ─────────────────────────────────────
GRUPOS = (
    ("Precio español (D, D-1, D-6)", lambda c: c.startswith("es_esios_")),
    ("Precios europeos D y diferenciales", lambda c: c.endswith("_entsoe_D") or c.startswith("spread_")),
    ("Previsiones REE (demanda, eólica, solar)", lambda c: c.startswith("ree_") and c.endswith("_prev")),
    ("Meteorología prevista", lambda c: c.endswith("_meteo")),
    ("Gas MIBGAS", lambda c: c == "gas_mibgas"),
    ("Calendario y hora", lambda c: c.startswith("d1_") or c in ("hora", "hora_sin", "hora_cos")),
    ("Programas PDBC/PBF de D", lambda c: c.startswith(("pdbc_", "pbfli_", "bil_")) and c.endswith("_D")),
    ("Generación, demanda y flujos reales D-1/D-6", lambda c: c.endswith(("_Dm1", "_Dm6"))),
    ("Capacidades (disponible e instalada)", lambda c: c.startswith(("capdisp_", "capinst_"))),
)
OTROS = "Otros"


def grupo(c: str) -> str:
    return next((n for n, regla in GRUPOS if regla(c)), OTROS)


def mae(y, p) -> float:
    return float(np.nanmean(np.abs(np.asarray(y) - np.asarray(p))))


def main():
    booster = lgb.Booster(model_file=str(SAL / f"{MODELO}.txt"))
    feats = booster.feature_name()
    extras = json.loads((SAL / "extras_lgbm_nucleo.json").read_text(encoding="utf-8"))["features"]
    assert feats == extras, "las features del booster no coinciden con extras_lgbm_nucleo.json"

    fuera = V2.columnas_vetadas("nucleo", V2.VETOS_DEFECTO)
    P = V2.cargar_plana("nucleo", fuera)

    res = {"modelo": MODELO, "n_features": len(feats), "n_arboles": booster.num_trees()}
    tramos = {}
    for tramo in ("val", "test"):
        pred_ok = pd.read_csv(SAL / f"pred_{tramo}_{MODELO}.csv", index_col=0, parse_dates=True)
        real = pd.read_csv(SAL / f"ref_{tramo}_real.csv", index_col=0, parse_dates=True)
        fechas = pred_ok.index
        X = V2.filas(P, fechas, feats)
        p = booster.predict(X).reshape(-1, 24)
        dif = float(np.abs(p - pred_ok.values).max())
        res[f"{tramo}_max_dif_vs_guardada"] = dif
        res[f"{tramo}_MAE"] = mae(real.loc[fechas].values, p)
        res[f"{tramo}_dias"] = len(fechas)
        tramos[tramo] = (X, p, real.loc[fechas].values, fechas)
        print(f"{tramo}: {len(fechas)} dias, max |pred - guardada| = {dif:.2e}, MAE = {res[f'{tramo}_MAE']:.3f}")

    # ── 1. SHAP ─────────────────────────────────────────────────────────────────────────────
    def shap(X):
        C = booster.predict(X, pred_contrib=True)
        return C[:, :-1], C[:, -1]

    tabla = pd.DataFrame(index=pd.Index(feats, name="variable"))
    tabla["grupo"] = [grupo(c) for c in feats]
    shap_test = None
    for tramo in ("test", "val"):
        X, p, _, _ = tramos[tramo]
        S, base = shap(X)
        err = float(np.abs(S.sum(1) + base - p.ravel()).max())
        res[f"{tramo}_shap_aditividad_max_err"] = err
        res["valor_esperado_base"] = float(base[0])
        tabla[f"shap_abs_medio_{tramo}"] = np.abs(S).mean(0)
        tabla[f"shap_medio_{tramo}"] = S.mean(0)
        tabla[f"proporcion_{tramo}"] = tabla[f"shap_abs_medio_{tramo}"] / tabla[f"shap_abs_medio_{tramo}"].sum()
        tabla[f"rango_{tramo}"] = tabla[f"shap_abs_medio_{tramo}"].rank(ascending=False, method="min").astype(int)
        if tramo == "test":
            shap_test = S
    # Entrenamiento, para separar "que usa el modelo al aprender" de "que pesa en test"
    tr = np.asarray(P.index.get_level_values(0) <= pd.Timestamp(TRAIN_END))
    Xtr = P.loc[tr, feats].astype("float64")
    Str, _ = shap(Xtr)
    tabla["shap_abs_medio_train"] = np.abs(Str).mean(0)
    tabla["proporcion_train"] = tabla["shap_abs_medio_train"] / tabla["shap_abs_medio_train"].sum()
    tabla["rango_train"] = tabla["shap_abs_medio_train"].rank(ascending=False, method="min").astype(int)
    res["train_filas_shap"] = int(tr.sum())

    # ── 3. Ganancia ─────────────────────────────────────────────────────────────────────────
    gan = V2.importancias_lgbm(SAL)["ganancia_media"]
    tabla["ganancia_media_3s"] = gan.reindex(feats).values
    tabla["proporcion_ganancia"] = tabla["ganancia_media_3s"] / tabla["ganancia_media_3s"].sum()
    tabla["rango_ganancia"] = tabla["ganancia_media_3s"].rank(ascending=False, method="min").astype(int)
    tabla["ganancia_s1"] = pd.Series(booster.feature_importance("gain"), index=feats)
    tabla["splits_s1"] = pd.Series(booster.feature_importance("split"), index=feats)
    tabla["rango_splits_s1"] = tabla["splits_s1"].rank(ascending=False, method="min").astype(int)
    tabla["dif_rango_ganancia_menos_shap_test"] = tabla["rango_ganancia"] - tabla["rango_test"]

    tabla = tabla.sort_values("shap_abs_medio_test", ascending=False)
    tabla.round(6).to_csv(OUT / "shap_lgbm_variables.csv", encoding="utf-8")

    from scipy.stats import spearmanr
    res["spearman_rango_shap_val_vs_test"] = float(spearmanr(tabla.shap_abs_medio_val, tabla.shap_abs_medio_test)[0])
    res["spearman_rango_shap_train_vs_test"] = float(spearmanr(tabla.shap_abs_medio_train, tabla.shap_abs_medio_test)[0])
    res["spearman_rango_ganancia_vs_shap_test"] = float(spearmanr(tabla.ganancia_media_3s, tabla.shap_abs_medio_test)[0])
    res["spearman_rango_ganancia_vs_shap_train"] = float(spearmanr(tabla.ganancia_media_3s, tabla.shap_abs_medio_train)[0])
    top = lambda col, k=12: set(tabla[col].nlargest(k).index)  # noqa: E731
    res["top12_comunes_val_test"] = len(top("shap_abs_medio_val") & top("shap_abs_medio_test"))
    res["top12_comunes_ganancia_shap_test"] = len(top("ganancia_media_3s") & top("shap_abs_medio_test"))
    res["top10_shap_test"] = list(tabla.index[:10])
    res["top10_ganancia"] = list(tabla.ganancia_media_3s.nlargest(10).index)

    # ── 2. Grupos ───────────────────────────────────────────────────────────────────────────
    orden_grupos = [n for n, _ in GRUPOS] + [OTROS]
    g = tabla.groupby("grupo").agg(
        n_variables=("grupo", "size"),
        shap_abs_test=("shap_abs_medio_test", "sum"),
        shap_abs_val=("shap_abs_medio_val", "sum"),
        shap_abs_train=("shap_abs_medio_train", "sum"),
        ganancia=("ganancia_media_3s", "sum"),
    ).reindex(orden_grupos).dropna(subset=["n_variables"])
    for c, dst in (("shap_abs_test", "proporcion_test"), ("shap_abs_val", "proporcion_val"),
                   ("shap_abs_train", "proporcion_train"), ("ganancia", "proporcion_ganancia")):
        g[dst] = g[c] / g[c].sum()
    # |SHAP| del grupo sumando primero con signo dentro de la fila (evita que se compensen o
    # se dupliquen contribuciones opuestas de variables del mismo bloque)
    idx = {n: [feats.index(v) for v in tabla.index[tabla.grupo == n]] for n in g.index}
    abs_neto = {n: np.abs(shap_test[:, ix].sum(1)).mean() for n, ix in idx.items()}
    g["shap_abs_neto_fila_test"] = pd.Series(abs_neto)
    g["proporcion_neto_fila_test"] = g["shap_abs_neto_fila_test"] / g["shap_abs_neto_fila_test"].sum()
    g["variable_principal"] = [tabla.loc[tabla.grupo == n, "shap_abs_medio_test"].idxmax() for n in g.index]
    g["regla"] = [
        "prefijo es_esios_", "sufijo _entsoe_D o prefijo spread_", "prefijo ree_ y sufijo _prev",
        "sufijo _meteo", "gas_mibgas", "prefijo d1_ o hora_sin/hora_cos",
        "prefijo pdbc_/pbfli_/bil_ y sufijo _D", "sufijo _Dm1 o _Dm6 (tras las reglas anteriores)",
        "prefijo capdisp_ o capinst_", "resto"][:len(g)] if list(g.index) == orden_grupos[:len(g)] else ""
    g = g.sort_values("proporcion_test", ascending=False)
    g.index.name = "grupo"
    g.round(6).to_csv(OUT / "shap_lgbm_grupos.csv", encoding="utf-8")
    res["grupos_proporcion_test"] = g.proporcion_test.round(4).to_dict()

    # ── 4. Portugal frente a Espana ─────────────────────────────────────────────────────────
    pt, es = P["pt_entsoe_D"], P["es_esios_D"]
    fo = P.index.get_level_values(0)
    split = np.where(fo <= pd.Timestamp(TRAIN_END), "train", np.where(fo <= pd.Timestamp(VAL_END), "val", "test"))
    ib = {}
    for nombre, m in (("total", np.ones(len(P), bool)), ("train", split == "train"),
                      ("val", split == "val"), ("test", split == "test")):
        d = (pt[m] - es[m]).abs()
        ib[nombre] = {
            "horas": int(m.sum()),
            "pearson": float(np.corrcoef(pt[m], es[m])[0, 1]),
            "spearman": float(spearmanr(pt[m], es[m])[0]),
            "pct_identicas_tol_0.01": float((d <= 0.01).mean() * 100),
            "dif_abs_media_cuando_difieren": float(d[d > 0.01].mean()) if (d > 0.01).any() else 0.0,
            "dif_abs_p95": float(d.quantile(0.95)),
        }
    anual = (pd.DataFrame({"anio": fo.year, "ident": ((pt - es).abs() <= 0.01).values})
             .groupby("anio").ident.mean().mul(100).round(2).to_dict())
    ib["pct_identicas_por_anio"] = {int(k): v for k, v in anual.items()}
    sp = P["spread_es_pt_D"]
    ib["spread_es_pt_D_igual_es_menos_pt_pct"] = float(((sp - (es - pt)).abs() <= 0.011).mean() * 100)
    ib["corr_pt_entsoe_D_con_target"] = float(np.corrcoef(pt, P["target_price"])[0, 1])
    ib["corr_es_esios_D_con_target"] = float(np.corrcoef(es, P["target_price"])[0, 1])
    # SHAP de ambas en test
    ipt, ies = feats.index("pt_entsoe_D"), feats.index("es_esios_D")
    ib["test_corr_shap_pt_vs_shap_es"] = float(np.corrcoef(shap_test[:, ipt], shap_test[:, ies])[0, 1])
    ib["test_proporcion_shap_pt"] = float(tabla.loc["pt_entsoe_D", "proporcion_test"])
    ib["test_proporcion_shap_es"] = float(tabla.loc["es_esios_D", "proporcion_test"])
    ib["test_proporcion_shap_suma"] = ib["test_proporcion_shap_pt"] + ib["test_proporcion_shap_es"]
    ident_test = ((tramos["test"][0]["pt_entsoe_D"] - tramos["test"][0]["es_esios_D"]).abs() <= 0.01).values
    ib["test_shap_abs_pt_horas_identicas"] = float(np.abs(shap_test[ident_test, ipt]).mean())
    ib["test_shap_abs_es_horas_identicas"] = float(np.abs(shap_test[ident_test, ies]).mean())
    ib["test_shap_abs_pt_horas_distintas"] = float(np.abs(shap_test[~ident_test, ipt]).mean())
    ib["test_shap_abs_es_horas_distintas"] = float(np.abs(shap_test[~ident_test, ies]).mean())
    ib["test_shap_abs_suma_pt_es_fila"] = float(np.abs(shap_test[:, [ipt, ies]].sum(1)).mean())

    # Huecos antes de imputar (capa bronce) y columnas de resolucion de ausentes
    try:
        aus = pd.read_csv(REPO / "data" / "gold" / "resolucion_ausentes_por_columna.csv").set_index("variable")
        ib["resolucion_ausentes"] = aus.loc[["pt_entsoe_D", "es_esios_D"]].to_dict(orient="index")
    except Exception as e:  # pragma: no cover
        ib["resolucion_ausentes"] = f"no disponible: {e}"
    try:
        cr = pd.read_parquet(REPO / "data" / "bronze" / "matriz_cruda.parquet",
                             columns=["fecha_objetivo", "hora", "pt_entsoe_D", "es_esios_D"])
        cr["fecha_objetivo"] = pd.to_datetime(cr.fecha_objetivo)
        ini = fo.min()             # ventana de la matriz nucleo
        ib["huecos_bronce_fuera_ventana"] = int((cr.fecha_objetivo < ini).sum() and
                                                (cr.loc[cr.fecha_objetivo < ini, ["pt_entsoe_D", "es_esios_D"]].isna().any(axis=1)).sum())
        cr = cr[cr.fecha_objetivo >= ini]
        hueco = cr[cr.pt_entsoe_D.isna() | cr.es_esios_D.isna()].copy()
        hueco["falta_pt"] = hueco.pt_entsoe_D.isna()
        hueco["falta_es"] = hueco.es_esios_D.isna()
        clave = pd.MultiIndex.from_arrays([hueco.fecha_objetivo, hueco.hora.astype(int)])
        ok = clave.isin(P.index)
        hueco["pt_en_matriz"] = np.nan
        hueco["es_en_matriz"] = np.nan
        hueco.loc[ok, "pt_en_matriz"] = P.loc[clave[ok], "pt_entsoe_D"].values
        hueco.loc[ok, "es_en_matriz"] = P.loc[clave[ok], "es_esios_D"].values
        hueco["fila_en_matriz"] = ok
        hueco["fecha_objetivo"] = hueco.fecha_objetivo.dt.strftime("%Y-%m-%d")
        ib["huecos_bronce"] = hueco[["fecha_objetivo", "hora", "falta_pt", "falta_es", "fila_en_matriz",
                                     "pt_en_matriz", "es_en_matriz"]].to_dict(orient="records")
        ib["huecos_bronce_n_pt"] = int(hueco.falta_pt.sum())
        ib["huecos_bronce_n_es"] = int(hueco.falta_es.sum())
        ib["huecos_bronce_coinciden"] = int((hueco.falta_pt & hueco.falta_es).sum())
    except Exception as e:
        ib["huecos_bronce"] = f"no disponible: {e}"

    # Permutacion por dias en test
    X, p0, y, fechas = tramos["test"]
    nd = len(fechas)
    A = X.values.reshape(nd, 24, -1)
    base_mae = mae(y, p0)
    variantes = {
        "pt_entsoe_D": ["pt_entsoe_D"],
        "es_esios_D": ["es_esios_D"],
        "pt_entsoe_D + es_esios_D": ["pt_entsoe_D", "es_esios_D"],
        "pt_entsoe_D + es_esios_D + spread_es_pt_D": ["pt_entsoe_D", "es_esios_D", "spread_es_pt_D"],
        "es_esios_Dm1 (referencia)": ["es_esios_Dm1"],
        "gas_mibgas (referencia)": ["gas_mibgas"],
    }
    rng = np.random.default_rng(SEMILLA)
    perms = [rng.permutation(nd) for _ in range(N_PERM)]
    filas_perm = []
    for nombre, cols in variantes.items():
        ix = [feats.index(c) for c in cols]
        maes = []
        for pr in perms:           # misma permutacion para todas las columnas de la variante
            B = A.copy()
            B[:, :, ix] = A[pr][:, :, ix]
            pb = booster.predict(pd.DataFrame(B.reshape(nd * 24, -1), columns=feats)).reshape(nd, 24)
            maes.append(mae(y, pb))
        maes = np.array(maes)
        filas_perm.append({"variante": nombre, "columnas": " | ".join(cols), "MAE_base": base_mae,
                           "MAE_perm_media": maes.mean(), "MAE_perm_sd": maes.std(ddof=1),
                           "delta_MAE_media": maes.mean() - base_mae,
                           "delta_MAE_p05": np.quantile(maes, 0.05) - base_mae,
                           "delta_MAE_p95": np.quantile(maes, 0.95) - base_mae,
                           "delta_MAE_pct": (maes.mean() / base_mae - 1) * 100, "repeticiones": N_PERM})
        print(f"perm {nombre}: dMAE = {maes.mean() - base_mae:+.3f} (sd {maes.std(ddof=1):.3f})")
    perm = pd.DataFrame(filas_perm)
    perm.round(4).to_csv(OUT / "permutacion_pt_es_lgbm_test.csv", index=False, encoding="utf-8")
    ib["permutacion"] = perm.round(4).to_dict(orient="records")
    res["portugal_espana"] = ib

    # ── 5. EDA ──────────────────────────────────────────────────────────────────────────────
    ruta_eda = REPO / "eda" / "EDA_spearman_output" / "ranking_spearman.csv"
    eda = pd.read_csv(ruta_eda).set_index("variable")
    comunes = [c for c in feats if c in eda.index]
    cmp_ = tabla.loc[comunes, ["grupo", "shap_abs_medio_test", "rango_test"]].join(
        eda.loc[comunes, ["rho", "abs_rho", "frontera"]])
    cmp_["rango_eda_en_modelo"] = cmp_.abs_rho.rank(ascending=False, method="min").astype(int)
    cmp_["rango_eda_global"] = eda.abs_rho.rank(ascending=False, method="min").reindex(comunes).astype(int)
    cmp_ = cmp_.sort_values("rango_test")
    cmp_.round(6).to_csv(OUT / "shap_vs_eda_spearman.csv", encoding="utf-8")
    ge = cmp_.groupby("grupo").agg(abs_rho_max=("abs_rho", "max"), abs_rho_media=("abs_rho", "mean"),
                                   variable_rho_max=("abs_rho", "idxmax"))
    ge = ge.join(g[["proporcion_test"]]).sort_values("abs_rho_max", ascending=False)
    ge["rango_grupo_eda"] = ge.abs_rho_max.rank(ascending=False, method="min").astype(int)
    ge["rango_grupo_shap"] = ge.proporcion_test.rank(ascending=False, method="min").astype(int)
    ge.round(4).to_csv(OUT / "shap_vs_eda_grupos.csv", encoding="utf-8")
    res["eda"] = {
        "fuente": str(ruta_eda.relative_to(REPO)),
        "features_modelo_en_ranking": len(comunes),
        "features_modelo_fuera_ranking": [c for c in feats if c not in eda.index],
        "spearman_abs_rho_vs_shap_test": float(spearmanr(cmp_.abs_rho, cmp_.shap_abs_medio_test)[0]),
        "top12_comunes": len(set(cmp_.abs_rho.nlargest(12).index) & set(cmp_.shap_abs_medio_test.nlargest(12).index)),
        "top10_eda_en_modelo": list(cmp_.abs_rho.nlargest(10).index),
        "top10_eda_global": list(eda.abs_rho.nlargest(10).index),
        "frontera_eda_de_top10_shap": cmp_.frontera.head(10).to_dict(),
    }
    # es_esios_D es el precio de D (lag de 24 h del objetivo) y no el objetivo: se comprueba
    lag = P["target_price"].copy()
    lag.index = pd.MultiIndex.from_arrays([lag.index.get_level_values(0) + pd.Timedelta(days=1),
                                           lag.index.get_level_values(1)])
    j = pd.concat([P["es_esios_D"], lag.rename("target_dia_anterior")], axis=1, join="inner")
    res["eda"]["es_esios_D_igual_target_dia_anterior_pct"] = float(((j.es_esios_D - j.target_dia_anterior).abs() <= 0.01).mean() * 100)
    res["eda"]["es_esios_D_igual_target_mismo_dia_pct"] = float(((P.es_esios_D - P.target_price).abs() <= 0.01).mean() * 100)

    (OUT / "shap_lgbm_resumen.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=float),
                                                encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("portugal_espana", "eda")}, indent=1,
                     ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
