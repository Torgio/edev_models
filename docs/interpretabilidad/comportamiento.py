"""Comportamiento del ensemble final (ensemble_seleccion, finales_v2_nucleo).

Solo lee las predicciones guardadas; no reentrena ni toca el repo fuera de esta carpeta.
Salidas: comportamiento.csv, comportamiento.json, fig_comportamiento.png

D = ref_*_naive.csv (el precio de D alineado con D+1, el mismo `T.naive` que usa el pipeline).
"""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "data" / "gold" / "finales_v2_nucleo"
OUT = Path(__file__).resolve().parent

# Pesos exactos de la seleccion voraz (meta.json los guarda redondeados a 4 decimales).
PESOS = {"lgbm_nucleo__s1": 11, "denso__s2": 10, "lstm__s1": 3,
         "seq2seq_absoluto__s2": 3, "gru__s2": 2, "seq2seq__s1": 1}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def leer(nombre):
    return pd.read_csv(DIR / f"{nombre}.csv", index_col=0, parse_dates=True)


def copia(y, p, D):
    """Misma definicion que entrenar_finales_v2.copia, mas la pendiente de regresion."""
    dp, dr = (p - D).ravel(), (y - D).ravel()
    a, b = p - p.mean(1, keepdims=True), D - D.mean(1, keepdims=True)
    den = np.sqrt((a * a).sum(1) * (b * b).sum(1))
    forma = float(((a * b).sum(1)[den > 0] / den[den > 0]).mean())
    pend = float(np.cov(dp, dr)[0, 1] / dr.var(ddof=1))
    return {"forma_vs_D": forma,
            "movimiento_%": float(100 * np.abs(dp).mean() / np.abs(dr).mean()),
            "corr_cambio": float(np.corrcoef(dp, dr)[0, 1]) if dp.std() > 0 else np.nan,
            "pendiente_cambio": pend}


def captura(yt, yp):
    n = np.arange(len(yt))
    cap = ((yt[n, yp.argmax(1)] - yt[n, yp.argmin(1)]) / np.maximum(yt.max(1) - yt.min(1), .1))
    return cap, (np.abs(yt.argmax(1) - yp.argmax(1)) <= 1)


def analizar(corte):
    y = leer(f"ref_{corte}_real")
    D = leer(f"ref_{corte}_naive").values
    Y = y.values
    E = leer(f"pred_{corte}_ensemble_seleccion").values
    miembros = {k: leer(f"pred_{corte}_{k}").values for k in PESOS}
    E_chk = sum(PESOS[k] * miembros[k] for k in PESOS) / sum(PESOS.values())
    assert np.abs(E_chk - E).max() < 1e-8, "el ensemble guardado no es la media ponderada"

    filas = []
    # (1) copia
    for nombre, p in [("realidad", Y), ("ensemble_seleccion", E), ("persistencia", D),
                      *miembros.items()]:
        c = copia(Y, p, D)
        c.update(MAE=float(np.abs(p - Y).mean()))
        if nombre in PESOS:
            c["peso"] = PESOS[nombre] / 30
        for k, v in c.items():
            filas.append({"corte": corte, "bloque": "copia", "grupo": nombre, "metrica": k, "valor": v})

    # (2) por cuartil de cambio real del dia (cuartiles del propio corte)
    cambio = np.abs(Y - D).mean(1)
    q = pd.qcut(cambio, 4, labels=["Q1", "Q2", "Q3", "Q4"])
    lim = np.quantile(cambio, [0, .25, .5, .75, 1])
    mae_e, mae_n = np.abs(E - Y).mean(1), np.abs(D - Y).mean(1)
    cap_e, pic_e = captura(Y, E)
    cap_n, pic_n = captura(Y, D)
    cuart = []
    for i, g in enumerate(["Q1", "Q2", "Q3", "Q4"]):
        m = np.asarray(q == g)
        r = {"cuartil": g, "n_dias": int(m.sum()),
             "cambio_min": float(lim[i]), "cambio_max": float(lim[i + 1]),
             "cambio_medio": float(cambio[m].mean()),
             "MAE_ensemble": float(mae_e[m].mean()), "MAE_persistencia": float(mae_n[m].mean()),
             "mejora_vs_persistencia_%": float(100 * (mae_e[m].mean() / mae_n[m].mean() - 1)),
             "dias_ensemble_peor_%": float(100 * (mae_e[m] > mae_n[m]).mean()),
             "captura_ensemble_%": float(100 * cap_e[m].mean()),
             "captura_persistencia_%": float(100 * cap_n[m].mean()),
             "pico1h_ensemble_%": float(100 * pic_e[m].mean()),
             "pico1h_persistencia_%": float(100 * pic_n[m].mean())}
        cuart.append(r)
        for k, v in r.items():
            if k != "cuartil":
                filas.append({"corte": corte, "bloque": "cuartil_cambio", "grupo": g, "metrica": k, "valor": v})

    # por dia de la semana de D+1 (el indice de los CSV es la fecha objetivo)
    dow = y.index.dayofweek.values
    semana = []
    for d in range(7):
        m = dow == d
        r = {"dia": DIAS[d], "n_dias": int(m.sum()),
             "MAE_ensemble": float(mae_e[m].mean()), "MAE_persistencia": float(mae_n[m].mean()),
             "mejora_vs_persistencia_%": float(100 * (mae_e[m].mean() / mae_n[m].mean() - 1)),
             "cambio_medio": float(cambio[m].mean())}
        semana.append(r)
        for k, v in r.items():
            if k != "dia":
                filas.append({"corte": corte, "bloque": "dia_semana_D+1", "grupo": DIAS[d], "metrica": k, "valor": v})

    # (4) captura y pico global, y en el 10 % de dias de mayor cambio
    top10 = cambio >= np.quantile(cambio, .9)
    spread = {}
    for nom, m in [("todos", np.ones(len(Y), bool)), ("Q4_cambio", np.asarray(q == "Q4")),
                   ("top10_cambio", top10)]:
        spread[nom] = {"n_dias": int(m.sum()),
                       "captura_ensemble_%": float(100 * cap_e[m].mean()),
                       "captura_persistencia_%": float(100 * cap_n[m].mean()),
                       "pico1h_ensemble_%": float(100 * pic_e[m].mean()),
                       "pico1h_persistencia_%": float(100 * pic_n[m].mean()),
                       "MAE_ensemble": float(mae_e[m].mean()),
                       "MAE_persistencia": float(mae_n[m].mean()),
                       "dias_ensemble_peor_%": float(100 * (mae_e[m] > mae_n[m]).mean())}
        for k, v in spread[nom].items():
            filas.append({"corte": corte, "bloque": "spread_pico", "grupo": nom, "metrica": k, "valor": v})

    return dict(y=y, Y=Y, D=D, E=E, cambio=cambio, q=q, mae_e=mae_e, mae_n=mae_n,
                filas=filas, cuartiles=cuart, semana=semana, spread=spread)


val, test = analizar("val"), analizar("test")

# (3) amplificacion: D + a (pred - D), a por minimos cuadrados en VALIDACION
dp_v, dr_v = (val["E"] - val["D"]).ravel(), (val["Y"] - val["D"]).ravel()
a_val = float((dp_v * dr_v).sum() / (dp_v * dp_v).sum())
dp_t, dr_t = (test["E"] - test["D"]).ravel(), (test["Y"] - test["D"]).ravel()
a_test_oraculo = float((dp_t * dr_t).sum() / (dp_t * dp_t).sum())
amp = {"a_ajustado_val": a_val,
       "a_oraculo_test (solo referencia, no aplicable)": a_test_oraculo,
       "MAE_val_antes": float(np.abs(val["E"] - val["Y"]).mean()),
       "MAE_val_despues": float(np.abs(val["D"] + a_val * (val["E"] - val["D"]) - val["Y"]).mean()),
       "MAE_test_antes": float(np.abs(test["E"] - test["Y"]).mean()),
       "MAE_test_despues": float(np.abs(test["D"] + a_val * (test["E"] - test["D"]) - test["Y"]).mean()),
       "RMSE_test_antes": float(np.sqrt(((test["E"] - test["Y"]) ** 2).mean())),
       "RMSE_test_despues": float(np.sqrt(((test["D"] + a_val * (test["E"] - test["D"]) - test["Y"]) ** 2).mean()))}
# barrido corto para ver la forma de la curva en test (descriptivo)
amp["barrido_test"] = {f"{a:.2f}": float(np.abs(test["D"] + a * (test["E"] - test["D"]) - test["Y"]).mean())
                       for a in np.arange(0.8, 1.41, 0.05)}
amp["barrido_val"] = {f"{a:.2f}": float(np.abs(val["D"] + a * (val["E"] - val["D"]) - val["Y"]).mean())
                      for a in np.arange(0.8, 1.41, 0.05)}
filas_amp = [{"corte": "val->test", "bloque": "amplificacion", "grupo": "ensemble_seleccion",
              "metrica": k, "valor": v} for k, v in amp.items() if not k.startswith("barrido")]

# Dia ilustrativo, criterio explicito: entre los dias de test del cuartil superior de cambio
# real (Q4), el de cociente MAE_ensemble / MAE_persistencia mas cercano a la MEDIANA de ese
# cociente en Q4. Es un dia de gran cambio con un comportamiento relativo tipico: ni el mejor
# ni el peor. (El primer criterio probado, el dia de cambio mediano de Q4, caia en 2026-01-05,
# el segundo mejor dia del ensemble en Q4, y habria dado una imagen demasiado favorable.)
m4 = np.where(np.asarray(test["q"] == "Q4"))[0]
ratio4 = test["mae_e"][m4] / test["mae_n"][m4]
i_dia = int(m4[np.argmin(np.abs(ratio4 - np.median(ratio4)))])
fecha = test["y"].index[i_dia]
orden = m4[np.argsort(test["cambio"][m4])]
i_alt = int(orden[(len(orden) - 1) // 2])
dia = {"criterio": "dia de test del cuartil Q4 de cambio real cuyo cociente MAE_ensemble/MAE_persistencia "
                   "es el mas cercano a la mediana de ese cociente en Q4",
       "fecha_D+1": fecha.strftime("%Y-%m-%d"), "dia_semana_D+1": DIAS[fecha.dayofweek],
       "cambio_medio_dia": float(test["cambio"][i_dia]),
       "MAE_ensemble_dia": float(test["mae_e"][i_dia]),
       "MAE_persistencia_dia": float(test["mae_n"][i_dia]),
       "cociente_dia": float(test["mae_e"][i_dia] / test["mae_n"][i_dia]),
       "cociente_mediano_Q4": float(np.median(ratio4)),
       "MAE_ensemble_mediano_Q4": float(np.median(test["mae_e"][m4])),
       "percentil_MAE_ensemble_dia_en_Q4": float(100 * (test["mae_e"][m4] <= test["mae_e"][i_dia]).mean()),
       "n_dias_Q4": int(len(m4)),
       "alternativa_descartada": {
           "criterio": "cambio real mediano dentro de Q4",
           "fecha_D+1": test["y"].index[i_alt].strftime("%Y-%m-%d"),
           "MAE_ensemble_dia": float(test["mae_e"][i_alt]),
           "MAE_persistencia_dia": float(test["mae_n"][i_alt]),
           "percentil_MAE_ensemble_dia_en_Q4": float(100 * (test["mae_e"][m4] <= test["mae_e"][i_alt]).mean()),
           "motivo": "segundo mejor dia del ensemble en Q4: ilustracion sesgada a favor"}}
filas_dia = [{"corte": "test", "bloque": "dia_ilustrativo", "grupo": dia["fecha_D+1"],
              "metrica": k, "valor": v} for k, v in dia.items()
             if isinstance(v, (int, float))]

tabla = pd.DataFrame(val["filas"] + test["filas"] + filas_amp + filas_dia)
tabla.to_csv(OUT / "comportamiento.csv", index=False, float_format="%.4f")


def red(o):
    if isinstance(o, dict):
        return {k: red(v) for k, v in o.items()}
    if isinstance(o, list):
        return [red(v) for v in o]
    return round(o, 4) if isinstance(o, float) else o


def copia_dict(res, corte):
    t = pd.DataFrame(res["filas"])
    t = t[t.bloque == "copia"].pivot(index="grupo", columns="metrica", values="valor")
    return {g: {k: v for k, v in r.items() if pd.notna(v)} for g, r in t.to_dict("index").items()}


resumen = {
    "fuente": "data/gold/finales_v2_nucleo: pred_{val,test}_ensemble_seleccion.csv, pred_*_<miembro>.csv, "
              "ref_{val,test}_real.csv, ref_{val,test}_naive.csv",
    "pesos": {k: f"{v}/30" for k, v in PESOS.items()},
    "nota_D": "D = ref_*_naive (T.naive). En test 1 dia (2026-03-30) y en val 5 dias no coincide con el "
              "real del dia anterior (cambio de hora / huecos); se usa tal cual lo usa el pipeline.",
    "definiciones": {
        "forma_vs_D": "correlacion media del perfil horario sin nivel (pred - media diaria) con el de D",
        "movimiento_%": "100 * mean|pred-D| / mean|real-D|",
        "corr_cambio": "corr((pred-D), (real-D)) sobre las 24 h de todos los dias",
        "pendiente_cambio": "pendiente OLS (con intercepto) de (pred-D) sobre (real-D)",
        "captura_%": "(real[argmax pred] - real[argmin pred]) / (max real - min real), media diaria",
        "pico1h_%": "% dias con |argmax real - argmax pred| <= 1 h",
        "cuartil_cambio": "cuartiles, dentro de cada corte, de mean_h |real-D| del dia"},
    "copia": {"val": copia_dict(val, "val"), "test": copia_dict(test, "test")},
    "cuartiles_cambio": {"val": val["cuartiles"], "test": test["cuartiles"]},
    "dia_semana_D+1": {"val": val["semana"], "test": test["semana"]},
    "amplificacion": amp,
    "spread_pico": {"val": val["spread"], "test": test["spread"]},
    "dia_ilustrativo": dia,
}
(OUT / "comportamiento.json").write_text(json.dumps(red(resumen), indent=2, ensure_ascii=False),
                                          encoding="utf-8")

# ── Figura ────────────────────────────────────────────────────────────────────────────────
C_ENS, C_PER, C_REAL = "#2a78d6", "#9a9994", "#1f1f1e"
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#6b6a66", "axes.labelcolor": "#2b2b2a",
                     "xtick.color": "#4a4a48", "ytick.color": "#4a4a48",
                     "legend.frameon": False, "font.family": "DejaVu Sans"})
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16 / 2.54, 6.4 / 2.54),
                               gridspec_kw={"width_ratios": [1, 1.25]})

cq = test["cuartiles"]
x = np.arange(4)
w = 0.38
b1 = ax1.bar(x - w / 2 - 0.01, [r["MAE_persistencia"] for r in cq], w, color=C_PER, label="Persistencia (D)")
b2 = ax1.bar(x + w / 2 + 0.01, [r["MAE_ensemble"] for r in cq], w, color=C_ENS, label="Ensemble final")
for bars in (b1, b2):
    for b in bars:
        ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.5, f"{b.get_height():.1f}".replace(".", ","),
                 ha="center", va="bottom", fontsize=6.3, color="#2b2b2a")
ax1.set_xticks(x, [f"{r['cuartil']}\n{r['cambio_min']:.0f}–{r['cambio_max']:.0f}" for r in cq])
ax1.set_xlabel("Cuartil de cambio real |D+1 − D| (€/MWh)")
ax1.set_ylabel("MAE (€/MWh)")
ax1.grid(axis="y", color="#e4e3df", lw=0.6)
ax1.set_axisbelow(True)
ax1.set_ylim(0, max(r["MAE_persistencia"] for r in cq) * 1.15)
ax1.text(-0.2, 1.02, "(a)", transform=ax1.transAxes, fontsize=9, fontweight="bold")

h = np.arange(24)
ax2.plot(h, test["D"][i_dia], color=C_PER, lw=1.6, ls="--", label="D (persistencia)")
ax2.plot(h, test["Y"][i_dia], color=C_REAL, lw=1.8, label="Real D+1")
ax2.plot(h, test["E"][i_dia], color=C_ENS, lw=1.8, label="Ensemble D+1")
ax2.set_xticks(range(0, 24, 3))
ax2.set_xlim(0, 23)
ax2.set_xlabel(f"Hora de D+1 ({fecha.strftime('%d/%m/%Y')}, {DIAS[fecha.dayofweek]})")
ax2.set_ylabel("Precio (€/MWh)")
ax2.grid(color="#e4e3df", lw=0.6)
ax2.set_axisbelow(True)
# Una sola leyenda comun: el gris es D (persistencia) y el azul el ensemble en ambos paneles.
from matplotlib.lines import Line2D
fig.legend(handles=[Line2D([], [], color=C_PER, lw=1.8, ls="--", label="D / persistencia"),
                    Line2D([], [], color=C_REAL, lw=1.8, label="Real D+1"),
                    Line2D([], [], color=C_ENS, lw=1.8, label="Ensemble final")],
           loc="lower center", ncol=3, fontsize=7, handlelength=2.2, columnspacing=1.6,
           bbox_to_anchor=(0.5, 0.0))
ax2.text(-0.17, 1.02, "(b)", transform=ax2.transAxes, fontsize=9, fontweight="bold")

fig.set_size_inches(16 / 2.54, 7.2 / 2.54)
fig.tight_layout(w_pad=1.5, rect=(0, 0.07, 1, 1))
fig.savefig(OUT / "fig_comportamiento.png", dpi=300)

# ── Consola ───────────────────────────────────────────────────────────────────────────────
print(json.dumps(red({k: resumen[k] for k in ["copia", "amplificacion", "spread_pico", "dia_ilustrativo"]}),
                 indent=1, ensure_ascii=False))
print(pd.DataFrame(test["cuartiles"]).round(2).to_string())
print(pd.DataFrame(val["cuartiles"]).round(2).to_string())
print(pd.DataFrame(test["semana"]).round(2).to_string())
print(pd.DataFrame(val["semana"]).round(2).to_string())
