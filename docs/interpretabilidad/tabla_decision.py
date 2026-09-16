"""Tabla de decision de modelos, contraste diario en test, aportacion de miembros y figura.

Solo lee `data/gold/finales_v2_nucleo` y `data/gold/finales_v2_nucleo_anclasemanal`; todo lo
que escribe queda en `docs/interpretabilidad/`.

    python docs/interpretabilidad/tabla_decision.py

Salidas
    tabla_decision.csv            ensembles + 13 familias + persistencia + seleccion con ancla semanal
    contraste_diario.csv          MAE diario en test del seleccionado frente a alternativas,
                                  IC 95 % por bootstrap de bloques semanales (7 dias, 5000)
    mae_diario_test.csv           el MAE de cada dia de test por modelo (base del contraste)
    aportacion_miembros.csv       quitar cada miembro y renormalizar pesos (val y test)
    correlacion_errores_val.csv   correlacion de Pearson de los errores horarios en validacion
    curva_seleccion.csv           MAE de validacion en cada paso de la seleccion voraz
    cifras_decision.json          comprobaciones y cifras clave
    fig_decision.png              MAE de validacion frente a MAE de test
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import Counter
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import entrenar_finales as EF  # noqa: E402
import entrenar_finales_v2 as V2  # noqa: E402

OUT = Path(__file__).resolve().parent
DIR_D = REPO / "data" / "gold" / "finales_v2_nucleo"
DIR_S = REPO / "data" / "gold" / "finales_v2_nucleo_anclasemanal"
B, BLOQUE, SEMILLA_BOOT = 5000, 7, 20260915
PASOS = 30

NOMBRE = {
    "ensemble_seleccion": "Ensemble seleccionado (Caruana, ancla D)",
    "ensemble": "Ensemble de las 8 familias de red (media simple)",
    "ensemble_todos": "Ensemble de todas las familias (media simple, 12)",
    "ensemble_seleccion_anclasemanal": "Ensemble seleccionado con ancla semanal (descartado)",
    "denso": "Red densa (MLP)", "lgbm_nucleo": "LightGBM global (filas horarias)",
    "lstm": "LSTM", "boosting": "LightGBM por hora (24 modelos)", "gru": "GRU",
    "seq2seq": "Seq2seq (residuo)", "conv1d_lstm": "Conv1D-LSTM", "elasticnet": "Elastic Net",
    "simplernn": "RNN simple", "seq2seq_absoluto": "Seq2seq (precio absoluto)",
    "ridge": "Ridge", "sarimax": "SARIMAX", "sarima": "SARIMA",
    "naive (persistencia)": "Persistencia (precio de D)",
}


def leer(carpeta, nombre):
    return pd.read_csv(carpeta / f"{nombre}.csv", index_col=0)


def T_ligero(carpeta):
    """Lo unico que `V2._ensemble_seleccion` y `V2.medir` usan de T: y, va, te."""
    yv, yt = leer(carpeta, "ref_val_real"), leer(carpeta, "ref_test_real")
    n_v, n_t = len(yv), len(yt)
    return SimpleNamespace(
        y=np.vstack([yv.to_numpy(), yt.to_numpy()]),
        va=np.r_[np.ones(n_v, bool), np.zeros(n_t, bool)],
        te=np.r_[np.zeros(n_v, bool), np.ones(n_t, bool)],
        fechas_val=pd.to_datetime(yv.index), fechas_test=pd.to_datetime(yt.index))


def curva_voraz(d, carpeta, yv, pasos=PASOS):
    """Repite el bucle de `V2._ensemble_seleccion` guardando la curva y el orden de entrada."""
    c = d[d.familia.isin(V2.FAMILIAS) & d.MAE_val.notna()]
    mejor = c.loc[c.groupby("familia")["MAE_val"].idxmin()]
    claves = [f"{r.familia}__s{int(r.semilla) - V2.SEMILLA}" for r in mejor.itertuples()]
    pv = {k: leer(carpeta, f"pred_val_{k}").to_numpy() for k in claves}
    suma, filas = np.zeros(yv.shape), []
    for paso in range(1, pasos + 1):
        error = {k: float(np.mean(np.abs((suma + pv[k]) / paso - yv))) for k in claves}
        k = min(error, key=error.get)
        suma += pv[k]
        filas.append({"paso": paso, "entra": k, "MAE_val": round(error[k], 4)})
    return pd.DataFrame(filas), claves


def fraccion(w, n=PASOS):
    f = Fraction(w).limit_denominator(n)
    return f"{f.numerator * (n // f.denominator)}/{n}"


def main():
    warnings.simplefilter("ignore", RuntimeWarning)
    T = T_ligero(DIR_D)
    TS = T_ligero(DIR_S)
    yv, yt = T.y[T.va], T.y[T.te]
    meta = json.loads((DIR_D / "meta.json").read_text(encoding="utf-8"))
    nv_test = meta["naive_test_MAE"]
    cifras = {"fuente": {"ancla_D": str(DIR_D.relative_to(REPO)),
                         "ancla_semanal": str(DIR_S.relative_to(REPO))}}

    # ── 0. Comprobaciones: la seleccion se reproduce desde los CSV guardados ─────────────
    d_D = pd.read_csv(DIR_D / "por_semilla.csv")
    pesos_D, e_va, e_te, ev_D, et_D = V2._ensemble_seleccion(d_D, V2.FAMILIAS, DIR_D, T, False,
                                                             pasos=PASOS)
    guard_va = leer(DIR_D, "pred_val_ensemble_seleccion").to_numpy()
    guard_te = leer(DIR_D, "pred_test_ensemble_seleccion").to_numpy()
    cifras["verificacion_ancla_D"] = {
        "pesos_recalculados": pesos_D, "pesos_meta": meta["ensembles"]["ensemble_seleccion"],
        "coinciden_pesos": pesos_D == meta["ensembles"]["ensemble_seleccion"],
        "MAE_val": round(ev_D["MAE"], 3), "MAE_test": round(et_D["MAE"], 3),
        "max_dif_pred_val_guardada": float(np.abs(e_va - guard_va).max()),
        "max_dif_pred_test_guardada": float(np.abs(e_te - guard_te).max())}
    curva_D, claves_D = curva_voraz(d_D, DIR_D, yv)
    curva_D.insert(0, "ancla", "D")

    same_real = (np.allclose(TS.y, T.y) and (TS.fechas_val == T.fechas_val).all()
                 and (TS.fechas_test == T.fechas_test).all())
    d_S = pd.read_csv(DIR_S / "por_semilla.csv")
    pesos_S, s_va, s_te, ev_S, et_S = V2._ensemble_seleccion(d_S, V2.FAMILIAS, DIR_S, TS, False,
                                                             pasos=PASOS)
    curva_S, _ = curva_voraz(d_S, DIR_S, TS.y[TS.va])
    curva_S.insert(0, "ancla", "semanal")
    pd.concat([curva_D, curva_S]).to_csv(OUT / "curva_seleccion.csv", index=False)
    cifras["ancla_semanal"] = {
        "mismo_real_y_fechas_que_ancla_D": bool(same_real), "pesos": pesos_S,
        "MAE_val": round(ev_S["MAE"], 3), "MAE_test": round(et_S["MAE"], 3),
        "coincide_con_docstring_11833_12165": (round(ev_S["MAE"], 3) == 11.833
                                               and round(et_S["MAE"], 3) == 12.165)}
    for nombre, cv in (("curva_ancla_D", curva_D), ("curva_ancla_semanal", curva_S)):
        i = int(cv.MAE_val.idxmin())
        cifras[nombre] = {"paso_minimo": int(cv.paso[i]), "MAE_min": float(cv.MAE_val[i]),
                          "MAE_paso_10": float(cv.MAE_val[9]), "MAE_paso_20": float(cv.MAE_val[19]),
                          "MAE_paso_30": float(cv.MAE_val[29]),
                          "minimo_en_el_ultimo_paso": int(cv.paso[i]) == PASOS}

    # ── 1. Tabla de decision ──────────────────────────────────────────────────────────────
    res = pd.read_csv(DIR_D / "resumen.csv", index_col=0)
    cols = ["tipo", "n", "MAE_val", "sd_val", "MAE_test", "sd_test", "captura_test", "pico_1h",
            "vs_naive_test_%"]
    tabla = res[cols].copy()
    tabla.loc["ensemble_seleccion_anclasemanal"] = {
        "tipo": "ensemble", "n": len(pesos_S), "MAE_val": round(ev_S["MAE"], 3), "sd_val": np.nan,
        "MAE_test": round(et_S["MAE"], 3), "sd_test": np.nan,
        "captura_test": round(et_S["captura_%"], 3), "pico_1h": round(et_S["pico_1h_%"], 3),
        "vs_naive_test_%": round(100 * (et_S["MAE"] / nv_test - 1), 1)}
    # representante de cada familia: la semilla con menor MAE de validacion (la que compite en
    # la seleccion voraz)
    rep = d_D.loc[d_D.groupby("familia")["MAE_val"].idxmin()].set_index("familia")
    pesos = meta["ensembles"]["ensemble_seleccion"]
    tabla.insert(0, "descripcion", [NOMBRE.get(i, i) for i in tabla.index])
    tabla["representante_val"] = ""
    tabla["MAE_val_representante"] = np.nan
    tabla["MAE_test_representante"] = np.nan
    tabla["miembro_seleccion"] = "no"
    tabla["peso"] = 0.0
    tabla["peso_de_30"] = ""
    for fam, r in rep.iterrows():
        k = f"{fam}__s{int(r.semilla) - V2.SEMILLA}"
        tabla.loc[fam, ["representante_val"]] = k
        tabla.loc[fam, ["MAE_val_representante", "MAE_test_representante"]] = [r.MAE_val, r.MAE_test]
        if k in pesos:
            tabla.loc[fam, ["miembro_seleccion", "peso", "peso_de_30"]] = ["si", pesos[k],
                                                                           fraccion(pesos[k])]
    no_familia = tabla.tipo.isin(["ensemble", "referencia"])
    tabla.loc[no_familia, "miembro_seleccion"] = "-"
    tabla.loc[no_familia, "peso"] = np.nan
    tabla.loc["ensemble_seleccion", ["peso", "peso_de_30"]] = [1.0, "30/30"]
    tabla["rango_val"] = tabla.MAE_val.rank(method="min").astype(int)
    tabla["rango_test"] = tabla.MAE_test.rank(method="min").astype(int)
    tabla = tabla.round({"MAE_val": 3, "sd_val": 3, "MAE_test": 3, "sd_test": 3,
                         "captura_test": 2, "pico_1h": 2, "vs_naive_test_%": 1,
                         "MAE_val_representante": 3, "MAE_test_representante": 3, "peso": 4})
    orden = (["ensemble_seleccion", "ensemble", "ensemble_todos", "ensemble_seleccion_anclasemanal"]
             + list(res[~res.tipo.isin(["ensemble", "referencia"])].sort_values("MAE_val").index)
             + ["naive (persistencia)"])
    tabla = tabla.loc[orden]
    tabla.index.name = "modelo"
    tabla.to_csv(OUT / "tabla_decision.csv")

    # ── 2. Contraste diario en test ─────────────────────────────────────────────────────
    def test_pred(k):
        return leer(DIR_D, f"pred_test_{k}").to_numpy()

    gru_rep = f"gru__s{int(rep.loc['gru', 'semilla']) - V2.SEMILLA}"
    lgb_rep = f"lgbm_nucleo__s{int(rep.loc['lgbm_nucleo', 'semilla']) - V2.SEMILLA}"
    rivales = {
        "ensemble_todos": ("Ensemble de todas las familias (12, media simple)", test_pred("ensemble_todos")),
        "ensemble": ("Ensemble de las 8 familias de red (media simple)", test_pred("ensemble")),
        gru_rep: ("GRU, semilla con menor MAE de validacion (miembro 2/30)", test_pred(gru_rep)),
        "gru_media3": ("GRU, media de las predicciones de las 3 semillas",
                       np.mean([test_pred(f"gru__s{s}") for s in range(3)], axis=0)),
        lgb_rep: ("LightGBM global, semilla con menor MAE de validacion (miembro 11/30)",
                  test_pred(lgb_rep)),
        "lgbm_nucleo_media3": ("LightGBM global, media de las predicciones de las 3 semillas",
                               np.mean([test_pred(f"lgbm_nucleo__s{s}") for s in range(3)], axis=0)),
        "naive (persistencia)": ("Persistencia (precio de D)", leer(DIR_D, "ref_test_naive").to_numpy()),
        "ensemble_seleccion_anclasemanal": ("Ensemble seleccionado con ancla semanal", s_te),
    }
    mae_sel = np.abs(guard_te - yt).mean(1)
    n = len(yt)
    rng = np.random.default_rng(SEMILLA_BOOT)
    nb = int(np.ceil(n / BLOQUE))
    inicios = rng.integers(0, n - BLOQUE + 1, size=(B, nb))
    idx = (inicios[:, :, None] + np.arange(BLOQUE)).reshape(B, -1)[:, :n]
    diario = pd.DataFrame({"ensemble_seleccion": mae_sel}, index=T.fechas_test.date)
    filas = []
    for k, (desc, p) in rivales.items():
        mae_o = np.abs(p - yt).mean(1)
        diario[k] = mae_o
        dif = mae_o - mae_sel                     # > 0: gana el seleccionado
        gana = (mae_sel < mae_o).astype(float)
        bd, bg = dif[idx].mean(1), gana[idx].mean(1)
        filas.append({
            "rival": k, "descripcion": desc, "n_dias": n,
            "MAE_test_seleccion": round(mae_sel.mean(), 3), "MAE_test_rival": round(mae_o.mean(), 3),
            "dif_media_rival_menos_sel": round(dif.mean(), 3),
            "IC95_inf": round(np.percentile(bd, 2.5), 3), "IC95_sup": round(np.percentile(bd, 97.5), 3),
            "dif_mediana": round(np.median(dif), 3),
            "mejora_relativa_%": round(100 * dif.mean() / mae_o.mean(), 1),
            "pct_dias_gana_sel": round(100 * gana.mean(), 1),
            "pct_IC95_inf": round(100 * np.percentile(bg, 2.5), 1),
            "pct_IC95_sup": round(100 * np.percentile(bg, 97.5), 1),
            "frac_remuestreos_dif_le_0": round(float((bd <= 0).mean()), 4),
            "IC_excluye_0": bool(np.percentile(bd, 2.5) > 0 or np.percentile(bd, 97.5) < 0)})
    contraste = pd.DataFrame(filas)
    contraste["bootstrap"] = f"bloques moviles de {BLOQUE} dias, {B} remuestreos, semilla {SEMILLA_BOOT}"
    contraste.to_csv(OUT / "contraste_diario.csv", index=False)
    diario.index.name = "fecha"
    diario.round(4).to_csv(OUT / "mae_diario_test.csv")

    # ── 3. Aportacion de cada miembro y correlacion de errores (validacion) ───────────────
    pv = {k: leer(DIR_D, f"pred_val_{k}").to_numpy() for k in pesos}
    pt = {k: leer(DIR_D, f"pred_test_{k}").to_numpy() for k in pesos}

    def mezcla(w, P):
        tot = sum(w.values())
        return sum(P[k] * v / tot for k, v in w.items())

    full_v = EF.metricas(yv, mezcla(pesos, pv))
    full_t = EF.metricas(yt, mezcla(pesos, pt))
    ap = [{"quitado": "(ninguno: seleccion completa)", "peso": 1.0, "peso_de_30": "30/30",
           "MAE_val": round(full_v["MAE"], 3), "MAE_test": round(full_t["MAE"], 3),
           "delta_val": 0.0, "delta_test": 0.0,
           "MAE_val_miembro_solo": np.nan, "MAE_test_miembro_solo": np.nan}]
    for k in pesos:
        w = {j: v for j, v in pesos.items() if j != k}
        mv, mt = EF.metricas(yv, mezcla(w, pv)), EF.metricas(yt, mezcla(w, pt))
        ap.append({"quitado": k, "peso": pesos[k], "peso_de_30": fraccion(pesos[k]),
                   "MAE_val": round(mv["MAE"], 3), "MAE_test": round(mt["MAE"], 3),
                   "delta_val": round(mv["MAE"] - full_v["MAE"], 3),
                   "delta_test": round(mt["MAE"] - full_t["MAE"], 3),
                   "MAE_val_miembro_solo": round(EF.metricas(yv, pv[k])["MAE"], 3),
                   "MAE_test_miembro_solo": round(EF.metricas(yt, pt[k])["MAE"], 3)})
    igual = {k: 1.0 for k in pesos}
    mv, mt = EF.metricas(yv, mezcla(igual, pv)), EF.metricas(yt, mezcla(igual, pt))
    ap.append({"quitado": "(referencia: los 6 miembros con pesos iguales)", "peso": np.nan,
               "peso_de_30": "5/30 cada uno", "MAE_val": round(mv["MAE"], 3),
               "MAE_test": round(mt["MAE"], 3), "delta_val": round(mv["MAE"] - full_v["MAE"], 3),
               "delta_test": round(mt["MAE"] - full_t["MAE"], 3),
               "MAE_val_miembro_solo": np.nan, "MAE_test_miembro_solo": np.nan})
    aport = pd.DataFrame(ap)
    aport.to_csv(OUT / "aportacion_miembros.csv", index=False)

    E = pd.DataFrame({k: (pv[k] - yv).ravel() for k in pesos})
    corr = E.corr()
    corr.round(3).to_csv(OUT / "correlacion_errores_val.csv")
    tri = corr.to_numpy()[np.triu_indices(len(corr), 1)]
    Et = pd.DataFrame({k: (pt[k] - yt).ravel() for k in pesos})
    tri_t = Et.corr().to_numpy()[np.triu_indices(len(corr), 1)]
    pares = corr.where(np.triu(np.ones(corr.shape, bool), 1)).stack()
    cifras["correlacion_errores"] = {
        "media_pares_val": round(float(tri.mean()), 3), "min_val": round(float(tri.min()), 3),
        "max_val": round(float(tri.max()), 3),
        "par_min_val": list(pares.idxmin()), "par_max_val": list(pares.idxmax()),
        "media_pares_test": round(float(tri_t.mean()), 3)}

    cifras["contraste_test"] = contraste.set_index("rival")[
        ["MAE_test_rival", "dif_media_rival_menos_sel", "IC95_inf", "IC95_sup",
         "pct_dias_gana_sel", "IC_excluye_0"]].to_dict(orient="index")
    cifras["representantes"] = {"gru": gru_rep, "lgbm_nucleo": lgb_rep}
    (OUT / "cifras_decision.json").write_text(json.dumps(cifras, indent=2, ensure_ascii=False,
                                                         default=str), encoding="utf-8")

    figura(tabla)
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(tabla.to_string())
        print(contraste.drop(columns=["descripcion", "bootstrap"]).to_string())
        print(aport.to_string())
        print(corr.round(3).to_string())
    print(json.dumps(cifras, indent=1, ensure_ascii=False, default=str))


# ── 4. Figura ─────────────────────────────────────────────────────────────────────────────
def figura(tabla):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.linewidth": 0.6,
                         "xtick.major.width": 0.6, "ytick.major.width": 0.6})
    C_SEL, C_MIE, C_OTRO, C_REF = "#a8431c", "#2f6690", "#8f8f8f", "#5a5a5a"
    ETQ = {"denso": "Red densa", "lgbm_nucleo": "LightGBM global", "lstm": "LSTM",
           "boosting": "LightGBM por hora", "gru": "GRU", "seq2seq": "Seq2seq",
           "conv1d_lstm": "Conv1D-LSTM", "elasticnet": "Elastic Net", "simplernn": "RNN simple",
           "seq2seq_absoluto": "Seq2seq absoluto", "ridge": "Ridge", "sarimax": "SARIMAX",
           "sarima": "SARIMA", "ensemble_seleccion": "Ensemble seleccionado",
           "ensemble": "Ensemble 8 redes", "ensemble_todos": "Ensemble 12 (todos)",
           "ensemble_seleccion_anclasemanal": "Seleccionado, ancla semanal"}
    # desplazamiento de la etiqueta en puntos (dx, dy, alineacion)
    OFF = {"ensemble_seleccion": (7, -9, "left"), "ensemble": (6, 5, "left"),
           "ensemble_todos": (6, -2, "left"), "ensemble_seleccion_anclasemanal": (-2, 9, "left"),
           "denso": (-6, 4, "right"), "lgbm_nucleo": (-6, 2, "right"), "lstm": (-6, 3, "right"),
           "boosting": (6, -9, "left"), "gru": (-12, 0, "right"), "seq2seq": (6, -10, "left"),
           "conv1d_lstm": (4, -13, "left"), "elasticnet": (-6, 4, "right"),
           "simplernn": (6, -6, "left"), "seq2seq_absoluto": (-6, 5, "right")}
    X0, X1, Y0, Y1 = 11.3, 16.85, 11.3, 17.0

    fig, ax = plt.subplots(figsize=(16 / 2.54, 11 / 2.54), dpi=300)
    ax.plot([X0, X1], [X0, X1], ls=":", lw=0.7, color="#b5b5b5", zorder=0)
    ax.text(16.35, 16.18, "val = test", color="#8a8a8a", fontsize=6.5, rotation=36,
            ha="right", va="bottom")
    naive = tabla.loc["naive (persistencia)"]
    ax.axhline(naive.MAE_test, ls="--", lw=0.7, color=C_REF, zorder=0)
    ax.annotate(f"Persistencia: test {naive.MAE_test:.2f} · val {naive.MAE_val:.2f} (fuera de escala) →"
                .replace(".", ","), xy=(X1, naive.MAE_test), xytext=(-3, 3),
                textcoords="offset points", ha="right", va="bottom", fontsize=6.5, color=C_REF)

    fuera = []
    for k, r in tabla.iterrows():
        if k == "naive (persistencia)":
            continue
        if r.MAE_val > X1 or r.MAE_test > Y1:
            fuera.append(f"{ETQ[k]} {r.MAE_val:.2f} / {r.MAE_test:.2f}".replace(".", ","))
            continue
        es_ens = r.tipo == "ensemble"
        if k == "ensemble_seleccion":
            kw = dict(marker="*", s=120, facecolor=C_SEL, edgecolor="white", lw=0.6, zorder=5)
        elif k == "ensemble_seleccion_anclasemanal":
            kw = dict(marker="*", s=70, facecolor="white", edgecolor=C_SEL, lw=0.8, zorder=4)
        elif es_ens:
            kw = dict(marker="s", s=26, facecolor="white", edgecolor=C_REF, lw=0.8, zorder=4)
        elif r.miembro_seleccion == "si":
            kw = dict(marker="o", s=30, facecolor=C_MIE, edgecolor="white", lw=0.6, zorder=4)
        else:
            kw = dict(marker="o", s=26, facecolor="white", edgecolor=C_OTRO, lw=0.8, zorder=3)
        if not es_ens and pd.notna(r.sd_val):
            col = C_MIE if r.miembro_seleccion == "si" else C_OTRO
            ax.errorbar(r.MAE_val, r.MAE_test, xerr=r.sd_val, yerr=r.sd_test, fmt="none",
                        ecolor=col, elinewidth=0.6, alpha=0.7, zorder=2)
        ax.scatter(r.MAE_val, r.MAE_test, **kw)
        txt = ETQ[k] + (f" ({r.peso_de_30})" if r.miembro_seleccion == "si" else "")
        dx, dy, ha = OFF.get(k, (6, 0, "left"))
        color = C_SEL if k.startswith("ensemble_seleccion") else (
            "#1f1f1f" if r.miembro_seleccion == "si" or k == "ensemble_seleccion" else "#555555")
        ax.annotate(txt, (r.MAE_val, r.MAE_test), xytext=(dx, dy), textcoords="offset points",
                    ha=ha, va="center", fontsize=6.5, color=color,
                    fontweight="bold" if k == "ensemble_seleccion" else "normal")

    ax.text(0.985, 0.03, "Fuera de escala (MAE val / test):\n" + "\n".join(fuera),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=6.3, color=C_REF,
            linespacing=1.35)
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    coma = FuncFormatter(lambda v, _: f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}".replace(".", ","))
    ax.xaxis.set_major_formatter(coma)
    ax.yaxis.set_major_formatter(coma)
    ax.set_xlabel("MAE en validación 2025 (€/MWh)")
    ax.set_ylabel("MAE en test ene–jul 2026 (€/MWh)")
    ax.grid(True, lw=0.4, color="#e4e4e4", zorder=-1)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    h = [Line2D([], [], marker="*", ls="", ms=10, mfc=C_SEL, mec="white", label="Ensemble seleccionado"),
         Line2D([], [], marker="o", ls="", ms=5, mfc=C_MIE, mec="white", label="Familia miembro (peso sobre 30)"),
         Line2D([], [], marker="o", ls="", ms=5, mfc="white", mec=C_OTRO, label="Otra familia"),
         Line2D([], [], marker="+", ls="", ms=7, mew=0.6, color=C_OTRO, label="± 1 sd entre semillas (media de 3)"),
         Line2D([], [], marker="s", ls="", ms=4.5, mfc="white", mec=C_REF, label="Ensemble de media simple"),
         Line2D([], [], marker="*", ls="", ms=8, mfc="white", mec=C_SEL, label="Seleccionado con ancla semanal")]
    ax.legend(handles=h, loc="upper left", frameon=False, fontsize=6.5, handletextpad=0.4,
              borderaxespad=0.3)
    fig.tight_layout(pad=0.3)
    fig.savefig(OUT / "fig_decision.png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main()
