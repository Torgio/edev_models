"""Importancia por permutacion del ensemble que va a produccion (`ensemble_seleccion`).

Explicacion agnostica del SISTEMA completo: no se mira dentro de ningun miembro, se estropea
un grupo de variables y se mide cuanto empeora el MAE de test de la combinacion ponderada.

PASOS
1. Reproducir. Se cargan los 6 miembros desde sus artefactos (`finales_v2_nucleo`), se
   reconstruyen sus entradas de test con el mismo codigo del entrenamiento y se combinan con
   los pesos de `meta.json`. Si la prediccion no coincide con `pred_test_ensemble_seleccion.csv`
   el script para: permutar un sistema que no es el guardado no explica nada.
2. Permutar. Para cada grupo de variables se baraja entre DIAS de test (212) el bloque entero
   del grupo: la misma permutacion de dias para todas sus columnas, y cada dia se lleva sus 24
   horas completas. El dia t recibe la informacion del grupo del dia pi(t) en TODOS los formatos
   a la vez (ver `permutar`).
3. Repetir con 3 semillas y medir el aumento del MAE de test del ensemble (y de cada miembro).

POR QUE SE PERMUTA LA MUESTRA Y NO LA FILA DE LA MATRIZ
La unidad de prediccion es el dia objetivo. En la matriz plana (LightGBM) el dia t son sus 24
filas, y permutar el grupo "a nivel de matriz" es exactamente mover esas 24 filas de columnas
del grupo del dia pi(t) al t. En los tensores:
  * X_dec y X_est son DEC[t] y EST[t]: filas del propio dia objetivo, asi que permutar
    X_dec[t] / X_est[t] es IDENTICO a permutar la matriz y reconstruir (se comprueba abajo
    llamando a `preparar` sobre una matriz permutada).
  * X_enc[t] es una ventana de 7 dias que sale de las filas t-6..t. Una fila de la matriz
    aparece en siete ventanas en posiciones distintas; permutar filas de la matriz mezclaria en
    una misma ventana dias de donantes distintos (y las primeras ventanas de test, que tiran de
    validacion, quedarian a medias). Se permuta la ventana entera: el dia t recibe la historia
    completa del grupo del dia pi(t), que es lo mismo que recibe LightGBM (sus `_Dm1`/`_Dm6`
    y `_D` son cortes de esa misma historia). Se comprueba que la ventana de precio del tensor
    contiene `es_esios_D`, `es_esios_Dm1` y `es_esios_Dm6` de la matriz plana en las posiciones
    esperadas, asi que el donante es coherente entre formatos.
  * Permutar sobre las entradas ya tipificadas equivale a tipificar las permutadas: el
    escalador es afin y columna a columna, ajustado en train, y el recorte a 10 sigmas es
    elemento a elemento.
  * `denso` usa `vista_plana`, que se recalcula sobre el tensor permutado.

EL ANCLA DEL RESIDUO
`es_esios_D` es a la vez entrada (canal de precio y columna del decoder) y ancla con la que
las redes residuales destipifican (y = pred*sd + mu + naive). Se permuta la ENTRADA y se deja el
ancla intacta: el ancla no es algo que la red haya aprendido a usar sino la definicion de su
salida, y moverla desplazaria el nivel de la curva por pura aritmetica, midiendo la resta y no
el uso de la informacion. Asi la comparacion con LightGBM y seq2seq_absoluto (sin ancla) es
justa. Como referencia se calcula tambien la variante con el ancla permutada.

Uso:  C:/Users/torgi/anaconda3/python.exe docs/interpretabilidad/permutacion_ensemble.py
Solo lee del repositorio; escribe en docs/interpretabilidad.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "scripts"))
SALIDA = Path(__file__).resolve().parent
FIN = REPO / "data" / "gold" / "finales_v2_nucleo"

SEMILLAS = (0, 1, 2)
TOL_REPRO = 0.05          # EUR/MWh: diferencia maxima admitida frente a lo guardado

GRUPOS = {                # clave -> etiqueta
    "precio_es": "Precio espanol (D, D-1, D-6)",
    "precios_europeos": "Precios europeos y spreads (D)",
    "prev_ree": "Previsiones REE (D+1)",
    "meteo_prevista": "Meteorologia prevista (D+1)",
    "gas": "Gas (MIBGAS)",
    "calendario": "Calendario (D+1)",
    "programas_D": "Programas de D (PDBC, PBF, bilaterales)",
    "real_Dm1_Dm6": "Generacion y flujos reales (D-1, D-6)",
    "capacidades": "Capacidades (instalada y disponible)",
}


def grupo_columna(c: str) -> str:
    """Grupo de una columna de la matriz plana (regla por prefijo/sufijo)."""
    if c in ("es_esios_D", "es_esios_Dm1", "es_esios_Dm6"):
        return "precio_es"
    if c.endswith("_entsoe_D") or c.startswith("spread_es_"):
        return "precios_europeos"
    if c.startswith("ree_") and c.endswith("_prev"):
        return "prev_ree"
    if c.endswith("_meteo") or c == "meteo_es_forecast":
        return "meteo_prevista"
    if c == "gas_mibgas":
        return "gas"
    if c.startswith("d1_") or c in ("hora_sin", "hora_cos"):
        return "calendario"
    if c.startswith(("pdbc_", "pbfli_", "bil_")) and c.endswith("_D"):
        return "programas_D"
    if c.endswith(("_Dm1", "_Dm6")):
        return "real_Dm1_Dm6"
    if c.startswith(("capinst_", "capdisp_")):
        return "capacidades"
    raise KeyError(f"columna sin grupo: {c}")


def grupo_canal(c: str) -> str:
    """Grupo de un canal del encoder: `precio` es el precio espanol, `x@D` un programa de D
    (columna `x_D`) y el resto la generacion/flujos observados (columna `x_Dm1`)."""
    if c == "precio":
        return "precio_es"
    return grupo_columna(c[:-2] + "_D" if c.endswith("@D") else c + "_Dm1")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:10]


def main():
    t0 = time.time()
    import keras
    import lightgbm as lgb
    import entrenar_finales as EF
    import entrenar_finales_v2 as V2
    import preparar_tensores as PT
    from preparar_tensores import preparar, residuo

    huellas = {f: md5(REPO / "scripts" / f) for f in
               ("preparar_tensores.py", "entrenar_finales.py", "entrenar_finales_v2.py")}
    meta = json.loads((FIN / "meta.json").read_text(encoding="utf-8"))
    pesos = meta["ensembles"]["ensemble_seleccion"]
    # los pesos del json van redondeados a 4 decimales; los exactos son n/30 (Caruana, 30 pasos)
    pesos = {k: round(w * 30) / 30 for k, w in pesos.items()}
    assert abs(sum(pesos.values()) - 1) < 1e-9, pesos
    print("pesos:", {k: f"{round(w * 30)}/30" for k, w in pesos.items()})

    # ── entradas, con el mismo codigo que el entrenamiento ─────────────────────────────────
    fuera = V2.columnas_vetadas("nucleo", V2.VETOS_DEFECTO)
    T = preparar("nucleo", verbose=False, excluir=(*V2.TRAYPORT, *fuera))
    P = V2.cargar_plana("nucleo", fuera)
    assert T.meta.get("hash") == meta["hash"], (T.meta.get("hash"), meta["hash"])
    te = T.te
    fechas = T.fechas[te]
    n = int(te.sum())
    _, _, mu_r, sd_r = residuo(T)
    mu_y, sd_y = float(T.y[T.tr].mean()), float(T.y[T.tr].std())
    for fam in ("denso", "lstm", "gru", "seq2seq", "seq2seq_absoluto"):
        pre = json.loads((FIN / f"{fam}.preprocesado.json").read_text(encoding="utf-8"))
        assert pre["canales"] == T.canales and pre["cols_dec"] == T.cols_dec \
            and pre["cols_est"] == T.cols_est, f"{fam}: columnas distintas del tensor"
        d = pre["destipificar"]
        ref = (mu_y, sd_y) if fam == "seq2seq_absoluto" else (mu_r, sd_r)
        assert abs(d["mu"] - ref[0]) < 1e-3 and abs(d["sd"] - ref[1]) < 1e-3, (fam, d, ref)

    booster = lgb.Booster(model_file=str(FIN / "lgbm_nucleo__s1.txt"))
    feats = booster.feature_name()
    extras = json.loads((FIN / "extras_lgbm_nucleo.json").read_text(encoding="utf-8"))
    assert feats == extras["features"], "features del booster != extras_lgbm_nucleo.json"

    Xe0, Xd0, Xs0 = T.X_enc[te], T.X_dec[te], T.X_est[te]
    F0 = V2.filas(P, fechas, feats).to_numpy("float64").reshape(n, 24, len(feats))
    naive0 = T.naive[te].astype("float64")
    y = T.y[te].astype("float64")
    real_csv = pd.read_csv(FIN / "ref_test_real.csv", index_col=0)
    assert (pd.to_datetime(real_csv.index) == pd.to_datetime(fechas)).all()
    assert np.abs(real_csv.to_numpy() - y).max() < 1e-3

    # ── coherencia entre formatos: la ventana de precio contiene D, D-1 y D-6 ──────────────
    s = T.esc["enc"]
    precio_ventana = (Xe0[..., 0] * s.sd.ravel()[0] + s.mu.ravel()[0]).reshape(n, 7, 24)
    iD, iD1, iD6 = (feats.index(c) for c in ("es_esios_D", "es_esios_Dm1", "es_esios_Dm6"))

    def resumen_dif(a, b, tol=1e-2):
        """Max |dif| y en que (dia objetivo, hora) caen las celdas que no coinciden."""
        dif = np.abs(a - b)
        malas = np.argwhere(dif > tol)
        return {"max_abs_dif": float(dif.max()), "celdas_distintas": int(len(malas)),
                "de": int(dif.size),
                "donde": sorted({f"{pd.Timestamp(fechas[i]).date()} h{h:02d}" for i, h in malas})[:10],
                "max_abs_dif_resto": float(np.where(dif > tol, 0, dif).max())}

    coherencia = {
        "ventana_dia_-1_vs_es_esios_D": resumen_dif(precio_ventana[:, 6], F0[:, :, iD]),
        "ventana_dia_-2_vs_es_esios_Dm1": resumen_dif(precio_ventana[:, 5], F0[:, :, iD1]),
        "ventana_dia_-7_vs_es_esios_Dm6": resumen_dif(precio_ventana[:, 0], F0[:, :, iD6]),
        "naive_vs_es_esios_D_plana": resumen_dif(naive0, F0[:, :, iD]),
        "nota": ("la ventana de precio del tensor sale de target_price (hora 2 del domingo de "
                 "marzo interpolada por preparar) y la plana de es_esios_*; solo difieren en "
                 "las celdas listadas en 'donde'"),
    }
    print("coherencia de formatos (max |dif|, EUR/MWh, sin recorte a 10 sigmas):", coherencia)

    # ── miembros ────────────────────────────────────────────────────────────────────────────
    redes = {k: keras.models.load_model(FIN / f"{k}.keras", compile=False)
             for k in pesos if not k.startswith("lgbm")}

    def predecir(Xe, Xd, Xs, F, naive):
        out = {}
        for k in pesos:
            fam = k.split("__")[0]
            if fam == "lgbm_nucleo":
                out[k] = booster.predict(F.reshape(n * 24, -1)).reshape(n, 24)
                continue
            if fam == "denso":
                x = EF.vista_plana(SimpleNamespace(X_enc=Xe, X_dec=Xd, X_est=Xs))
            else:
                x = {"hist": Xe, "fut": Xd, "est": Xs}
            p = redes[k].predict(x, verbose=0, batch_size=512).astype("float64")
            out[k] = p * sd_y + mu_y if fam == "seq2seq_absoluto" else p * sd_r + mu_r + naive
        out["ensemble_seleccion"] = sum(out[k] * w for k, w in pesos.items())
        return out

    mae = lambda p: float(np.abs(p - y).mean())
    base = predecir(Xe0, Xd0, Xs0, F0, naive0)
    repro = {}
    for k in [*pesos, "ensemble_seleccion"]:
        g = pd.read_csv(FIN / f"pred_test_{k}.csv", index_col=0)
        assert (pd.to_datetime(g.index) == pd.to_datetime(fechas)).all()
        dif = np.abs(base[k] - g.to_numpy())
        repro[k] = {"max_abs_dif": float(dif.max()), "media_abs_dif": float(dif.mean()),
                    "MAE_reproducido": mae(base[k]), "MAE_guardado": mae(g.to_numpy())}
        print(f"   {k:22s} max|dif| {dif.max():.2e}  MAE {mae(base[k]):.4f} "
              f"(guardado {mae(g.to_numpy()):.4f})")
    if repro["ensemble_seleccion"]["max_abs_dif"] > TOL_REPRO:
        raise SystemExit("la reproduccion no coincide con lo guardado: no se sigue")
    mae_base = {k: mae(v) for k, v in base.items()}

    # ── indices de cada grupo en cada formato ──────────────────────────────────────────────
    idx = {g: {"plana": [i for i, c in enumerate(feats) if grupo_columna(c) == g],
               "enc": [i for i, c in enumerate(T.canales) if grupo_canal(c) == g],
               "dec": [i for i, c in enumerate(T.cols_dec) if grupo_columna(c) == g],
               "est": [i for i, c in enumerate(T.cols_est) if grupo_columna(c) == g]}
           for g in GRUPOS}
    for fmt, cols in (("plana", feats), ("enc", T.canales), ("dec", T.cols_dec),
                      ("est", T.cols_est)):
        usados = sorted(i for g in GRUPOS for i in idx[g][fmt])
        assert usados == list(range(len(cols))), f"{fmt}: columnas sin grupo o repetidas"
    columnas_grupo = {g: {"plana": [feats[i] for i in d["plana"]],
                          "encoder": [T.canales[i] for i in d["enc"]],
                          "decoder": [T.cols_dec[i] for i in d["dec"]],
                          "estaticos": [T.cols_est[i] for i in d["est"]]}
                      for g, d in idx.items()}

    def permutar(grupos, pi, ancla=False):
        """Entradas de test con los grupos barajados entre dias segun pi (misma pi en todos los
        formatos y columnas; cada dia arrastra sus 24 horas / su ventana de 168)."""
        grupos = [grupos] if isinstance(grupos, str) else grupos
        Xe, Xd, Xs, F = Xe0.copy(), Xd0.copy(), Xs0.copy(), F0.copy()
        for g in grupos:
            d = idx[g]
            if d["enc"]:
                Xe[:, :, d["enc"]] = Xe0[pi][:, :, d["enc"]]
            if d["dec"]:
                Xd[:, :, d["dec"]] = Xd0[pi][:, :, d["dec"]]
            if d["est"]:
                Xs[:, d["est"]] = Xs0[pi][:, d["est"]]
            if d["plana"]:
                F[:, :, d["plana"]] = F0[pi][:, :, d["plana"]]
        return Xe, Xd, Xs, F, (naive0[pi] if ancla else naive0)

    # ── comprobacion: permutar el tensor == permutar la matriz y llamar a `preparar` ───────
    # Grupos solo de decoder y estaticos (meteo y capacidades), donde la equivalencia es exacta.
    rng = np.random.default_rng(12345)
    pi_chk = rng.permutation(n)
    comprobacion = {}
    try:
        ruta = REPO / "data" / "gold" / "matriz_nucleo.parquet"
        try:
            df = pd.read_parquet(ruta)
        except Exception:
            df = pd.read_csv(ruta.with_suffix(".csv"), parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
        cols_chk = [c for c in df.columns if c in
                    {*columnas_grupo["meteo_prevista"]["decoder"],
                     *columnas_grupo["capacidades"]["estaticos"],
                     *columnas_grupo["capacidades"]["plana"], *columnas_grupo["meteo_prevista"]["plana"]}]
        dias_te = pd.to_datetime(fechas)
        mapa = dict(zip(dias_te, dias_te[pi_chk]))
        fo = pd.to_datetime(df.fecha_objetivo)
        donante = fo.map(lambda f: mapa.get(f, f))
        tabla = df.assign(_f=fo)[["_f", "hora", *cols_chk]].set_index(["_f", "hora"])
        nuevo = tabla.reindex(pd.MultiIndex.from_arrays([donante, df.hora])).to_numpy()
        df_mod = df.copy()
        df_mod[cols_chk] = nuevo              # hora 2 del donante de marzo -> NaN, se interpola
        orig_pq, orig_csv = pd.read_parquet, pd.read_csv
        try:
            pd.read_parquet = lambda *a, **k: df_mod.copy()
            Tm = preparar("nucleo", verbose=False, excluir=(*V2.TRAYPORT, *fuera))
            Pm = V2.cargar_plana("nucleo", fuera)
        finally:
            pd.read_parquet, pd.read_csv = orig_pq, orig_csv
        assert (Tm.fechas[Tm.te] == fechas).all() and Tm.cols_dec == T.cols_dec \
            and Tm.cols_est == T.cols_est
        Xe_p, Xd_p, Xs_p, F_p, _ = permutar(["meteo_prevista", "capacidades"], pi_chk)
        Fm = V2.filas(Pm, fechas, feats).to_numpy("float64").reshape(n, 24, len(feats))
        # dias tocados por el domingo de 23 h: el propio domingo y el que lo recibe de donante
        marzo = [i for i, f in enumerate(pd.to_datetime(fechas))
                 if not ((df.assign(_f=fo)._f == f).sum() == 24)]
        raros = sorted({*marzo, *[i for i in range(n) if pi_chk[i] in marzo]})
        resto = np.setdiff1d(np.arange(n), raros)

        def dmax(a, b):
            d_ = np.abs(a - b).reshape(n, -1)
            return {"max_abs_dif": float(d_.max()),
                    "dias_con_dif>1e-3": [str(pd.Timestamp(fechas[i]).date())
                                          for i in np.where(d_.max(1) > 1e-3)[0]],
                    "max_abs_dif_sin_dias_domingo_23h": float(d_[resto].max())}

        comprobacion = {
            "grupos": ["meteo_prevista", "capacidades"],
            "dias_domingo_23h_o_su_receptor": [str(pd.Timestamp(fechas[i]).date()) for i in raros],
            "X_dec_tipificado": dmax(Tm.X_dec[Tm.te], Xd_p),
            "X_est_tipificado": dmax(Tm.X_est[Tm.te], Xs_p),
            "X_enc_tipificado": dmax(Tm.X_enc[Tm.te], Xe_p),
            "plana_lgbm": dmax(Fm, F_p),
            "nota": ("en la matriz el domingo de marzo no tiene hora 2: al recibir o donar ese "
                     "dia, `preparar`/`filas` interpolan la hora 2 (o promedian 23 h) en vez de "
                     "copiar la del donante; fuera de esos dias la equivalencia es exacta"),
        }
        print("comprobacion matriz permutada vs tensor permutado:", comprobacion)
        del df, df_mod, Tm, Pm
    except Exception as e:                    # la comprobacion no debe tumbar el calculo
        comprobacion = {"error": f"{type(e).__name__}: {e}"}
        print("comprobacion no realizada:", comprobacion)

    # ── permutaciones ──────────────────────────────────────────────────────────────────────
    filas_ens, filas_mie, perms = [], [], {}
    casos = [(g, False) for g in GRUPOS] + [("precio_es", True)]
    for s in SEMILLAS:
        pi = np.random.default_rng(s).permutation(n)
        perms[str(s)] = pi.tolist()
        for g, ancla in casos:
            t1 = time.time()
            pred = predecir(*permutar(g, pi, ancla))
            clave = g + ("__ancla_permutada" if ancla else "")
            for k, p in pred.items():
                r = {"grupo": clave, "semilla": s, "modelo": k, "MAE_base": mae_base[k],
                     "MAE_permutado": mae(p), "aumento_MAE": mae(p) - mae_base[k]}
                (filas_ens if k == "ensemble_seleccion" else filas_mie).append(r)
            print(f"   semilla {s} {clave:30s} +{mae(pred['ensemble_seleccion']) - mae_base['ensemble_seleccion']:7.3f}"
                  f"  [{time.time() - t1:.1f} s]")

    E = pd.DataFrame(filas_ens)
    res = (E.groupby("grupo", sort=False)
           .agg(MAE_base=("MAE_base", "first"), MAE_permutado_media=("MAE_permutado", "mean"),
                aumento_MAE_media=("aumento_MAE", "mean"), aumento_MAE_min=("aumento_MAE", "min"),
                aumento_MAE_max=("aumento_MAE", "max")).reset_index())
    for s in SEMILLAS:
        res[f"aumento_MAE_s{s}"] = E[E.semilla == s].set_index("grupo").loc[res.grupo, "aumento_MAE"].to_numpy()
    res["aumento_%_media"] = 100 * res.aumento_MAE_media / res.MAE_base
    res["etiqueta"] = [GRUPOS.get(g, GRUPOS["precio_es"] + " + ancla permutada")
                       if g in GRUPOS or g.endswith("ancla_permutada") else g for g in res.grupo]
    res["variante"] = np.where(res.grupo.str.endswith("__ancla_permutada"), "referencia", "principal")
    for fmt, nombre in (("plana", "n_cols_lgbm"), ("enc", "n_canales_encoder"),
                        ("dec", "n_cols_decoder"), ("est", "n_cols_estaticos")):
        res[nombre] = [len(idx[g.split("__")[0]][fmt]) for g in res.grupo]
    res = res.sort_values(["variante", "aumento_MAE_media"], ascending=[True, False])
    cols = ["grupo", "etiqueta", "variante", "n_cols_lgbm", "n_canales_encoder", "n_cols_decoder",
            "n_cols_estaticos", "MAE_base", "MAE_permutado_media", "aumento_MAE_media",
            "aumento_MAE_min", "aumento_MAE_max", *[f"aumento_MAE_s{s}" for s in SEMILLAS],
            "aumento_%_media"]
    res[cols].round(4).to_csv(SALIDA / "permutacion_ensemble.csv", index=False)

    M = pd.DataFrame(filas_mie)
    rm = (M.groupby(["grupo", "modelo"], sort=False)
          .agg(peso=("modelo", lambda x: pesos[x.iloc[0]]), MAE_base=("MAE_base", "first"),
               aumento_MAE_media=("aumento_MAE", "mean"), aumento_MAE_min=("aumento_MAE", "min"),
               aumento_MAE_max=("aumento_MAE", "max")).reset_index())
    rm.round(4).to_csv(SALIDA / "permutacion_ensemble_miembros.csv", index=False)

    (SALIDA / "permutacion_ensemble_meta.json").write_text(json.dumps({
        "modelo": "ensemble_seleccion (finales_v2_nucleo)", "hash_matriz": meta["hash"],
        "pesos": {k: f"{round(w * 30)}/30" for k, w in pesos.items()},
        "dias_test": n, "desde": str(pd.Timestamp(fechas[0]).date()),
        "hasta": str(pd.Timestamp(fechas[-1]).date()),
        "MAE_base_test": mae_base, "reproduccion": repro, "tolerancia_reproduccion": TOL_REPRO,
        "coherencia_formatos": coherencia, "comprobacion_matriz_vs_tensor": comprobacion,
        "semillas": list(SEMILLAS),
        "metodo": ("Permutacion por dia de test del bloque del grupo, misma permutacion para "
                   "todas sus columnas y formatos (matriz plana 24 h, ventana de encoder 168 h, "
                   "decoder 24 h, estaticos); una permutacion por semilla compartida por todos "
                   "los grupos (diseno pareado). Ancla del residuo sin permutar salvo en la "
                   "variante __ancla_permutada."),
        "columnas_por_grupo": columnas_grupo, "huellas_scripts_md5": huellas,
        "permutaciones": perms, "segundos": round(time.time() - t0, 1),
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(res[cols[:2] + cols[9:12]].to_string(index=False))
    print(f"total {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
