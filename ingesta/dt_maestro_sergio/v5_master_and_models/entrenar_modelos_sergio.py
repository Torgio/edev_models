"""
TFM Energia UCM -- Entrenamiento y comparacion de modelos de precio D+1

Lee el CSV horario que produce `construir_dataset_maestro_sergio.py` y entrena la escalera
completa. Reescrito 23-ago-2026 para leer el CSV EN VEZ del .npz: asi hereda automaticamente
la poda de columnas, las variantes y los periodos excluidos, sin duplicar logica en dos sitios.

    python entrenar_modelos_sergio.py                        # compara las DOS variantes
    python entrenar_modelos_sergio.py --variante sin_2020    # solo una
    python entrenar_modelos_sergio.py --solo-baselines       # 5 s, sin TensorFlow
    python entrenar_modelos_sergio.py --encoder-solo-precio  # ablacion del encoder multicanal
    python entrenar_modelos_sergio.py --walk-forward         # validacion cruzada temporal

ESTRUCTURA DE ENTRADA (past covariates / future covariates)

  X_enc  (n, 24*V, C)  lo REALIZADO: precio + series reales horarias de los dias D-V .. D-1.
                       El dia D no entra en los canales reales -- a las 12:00 solo han ocurrido
                       sus horas 00-11, meterlo entero seria fuga de medio dia.
  X_dec  (n, 24, f)    lo PUBLICADO POR ADELANTADO sobre D+1: previsiones, NTC, mas el precio
                       del dia D alineado por hora (ese si se conoce: se caso ayer a las 12:00).
  X_est  (n, j)        lo CONSTANTE dentro del dia: PDBC, capacidad, commodities, calendario.
  y      (n, 24)       precio de D+1.

TRES DECISIONES Y SU MOTIVO

  - TARGET = RESIDUO frente al naive, no precio absoluto. Es lo que separo "no bate al
    baseline" de "lo bate": en absoluto ningun modelo llegaba (MLP 20,9 / Seq2Seq 27,2 frente a
    17,1 del naive) y con residuo el Seq2Seq bajo a 15,1. Train incluye la crisis del gas de
    2022 con media de 93 EUR/MWh y el test es 2026, medio sigma por debajo; una red
    regularizada tira hacia la media del target que ha visto, mientras que el naive es inmune
    porque no aprende ningun nivel. Al predecir la desviacion, el problema de regimen
    desaparece.
  - PERDIDA HUBER, no MSE. Los picos de 2022 (>500 EUR/MWh) dominarian el gradiente.
  - ESCALADO SOLO CON TRAIN, y descartando columnas sin varianza EN TRAIN. Una columna
    constante solo dentro de train (bateria: ESIOS 2166/2167 publica desde el 20-nov-2024 y
    train acaba el 31-dic) da sd=0, se divide entre el epsilon y val/test se multiplican por un
    millon. Paso: val_loss de 268.000 y MAE de 832.039 con la perdida de train en 0,02.
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
SEMILLA = 42
np.random.seed(SEMILLA)

VENTANA_DIAS = 7

# ══ FRONTERAS DEL SPLIT ══════════════════════════════════════════════════════════════════
# Cronologico y sin solapes: train -> validation -> test, siempre hacia adelante. Es lo que
# replica la situacion real (predecir un dia que aun no ha ocurrido) y por eso no se negocia:
# meter dias de test en train convierte el MAE final en una medida de interpolacion sobre un
# periodo ya visto, no de prediccion.
#
# NO tienen que ser del mismo tamaño, y no lo son (1.765 / 339 / 203 dias). Cada uno responde
# a una necesidad distinta:
#   train  -> cuantos mas dias mejor, con el limite del cambio de regimen.
#   val    -> lo que hace falta es PRECISION PARA DISTINGUIR MODELOS. Con n dias, el error
#             estandar del MAE es ~sigma/sqrt(n): con sigma~20 EUR/MWh y 339 dias sale +-1,1.
#             O sea que diferencias menores de ~1 EUR/MWh en validacion NO son distinguibles
#             del azar -- que es justo lo que se veia en la busqueda de hiperparametros.
#             Recortar validacion empeora esto: a 180 dias el error estandar sube a +-1,5.
#   test   -> solo tiene que bastar para reportar un numero creible. 203 dias sobra.
TRAIN_END, VAL_END = "2024-12-31", "2025-12-31"

# Reentreno final con train+val. Una vez ELEGIDO el modelo usando validacion, se reentrena con
# todo el historico hasta VAL_END y se evalua UNA VEZ en test. Es la practica estandar y es
# limpia: la seleccion se hizo sin ver test, y el modelo final aprovecha todos los datos
# disponibles hasta el momento de predecir -- lo mas cerca de test que se puede llegar sin
# fuga. Requiere fijar las epocas a mano, porque ya no queda validacion para EarlyStopping.
REENTRENAR_CON_VAL = True
# Apagon iberico: no es una observacion extrema que el modelo deba aprender, es una observacion
# de OTRO proceso. Se excluye el periodo y ademas las ventanas del encoder que lo pisen.
PERIODOS_EXCLUIDOS = [("2025-04-28", "2025-05-06")]


# ══════════════════════════════════════════════════════════════════════════════════════════
#  Carga y construccion de tensores
# ══════════════════════════════════════════════════════════════════════════════════════════

def construir(ruta: Path, ventana=VENTANA_DIAS, encoder_multicanal=True):
    df = pd.read_csv(ruta, parse_dates=["fecha_pred", "fecha_objetivo"])
    df = df.sort_values(["fecha_objetivo", "hora"]).reset_index(drop=True)
    print(f"\n{ruta.name}: {df.shape[0]:,} filas x {df.shape[1]} columnas "
          f"({df.fecha_objetivo.min().date()} -> {df.fecha_objetivo.max().date()})")

    # ── reparto por la FRONTERA DE FUGA (12:00 de D), no por tipo de dato ─────────────────
    #
    # DECODER: lo publicado por adelantado sobre D+1, mas lo del dia D que ya se conoce.
    #   `_prev_mw` / `_prev`  previsiones de REE (la tabla `forecast` usa `_prev` a secas)
    #   `ree_ntc_`            NTC
    #   `es_esios_D`          precio de hoy, alineado por hora
    #   `_entsoe_D`/`_omie_D` precios europeos del dia D. NUNCA de D+1: todas las zonas SDAC se
    #                         casan a la vez a las 12:00, asi que el precio frances de mañana no
    #                         existe cuando hay que predecir el español de mañana.
    #   `spread_es_`          diferencia con PT/FR/DE. Como NIVEL aportan poco (PT correlaciona
    #                         0,997), pero las horas de DESACOPLE son las de congestion, que son
    #                         las de precio extremo y las que peor predice el modelo.
    #   `_METEO_PERFECTA`     solo si el dataset se genero con ERA5_MODO="perfecto". Es fuga
    #                         deliberada: los resultados hay que etiquetarlos.
    cols_dec = ([c for c in df.columns if c.endswith(("_prev_mw", "_prev"))]
                + [c for c in df.columns if c.startswith("ree_ntc_")]
                + [c for c in df.columns if c == "es_esios_D"]
                + [c for c in df.columns if c.endswith(("_entsoe_D", "_omie_D"))]
                + [c for c in df.columns if c.startswith("spread_es_")]
                + [c for c in df.columns if c.endswith("_METEO_PERFECTA")]
                + [c for c in df.columns if c in ("hora_sin", "hora_cos")])
    cols_dec = list(dict.fromkeys(cols_dec))
    if any(c.endswith("_METEO_PERFECTA") for c in cols_dec):
        print("  *** ATENCION: el dataset lleva ERA5 en modo 'perfecto' (tiempo REAL de D+1).")
        print("      Es FUGA. Los resultados son la cota superior con prevision meteorologica")
        print("      perfecta, NO el modelo titular. Etiquetalos como tal. ***")

    # ENCODER: series horarias pasadas. `_Dm1` es el dia D-1 (ultima jornada real cerrada) y
    # `_Dm2` la meteo de D-2. Las dos se reconstruyen desplazando por su propia fecha.
    cols_dm1 = [c for c in df.columns if c.endswith("_Dm1") and c != "es_esios_Dm1"]
    cols_dm2 = [c for c in df.columns if c.endswith("_Dm2")]

    cols_est = [c for c in df.columns
                if c.startswith(("pdbc_", "capinst_", "capdisp_", "d1_"))
                or c in ("gas_mibgas", "co2_eua_dec", "gas_ttf_m1")]
    # La meteo de D-2 entra como estatico agregado por dia. Podria ir al encoder como canal,
    # pero seria la MISMA serie que `_met_Dm1` desplazada un dia mas: el encoder ya la ve en su
    # ventana de 168 h. Aqui aporta el nivel del dia anterior sin duplicar la secuencia.
    cols_est += cols_dm2
    if encoder_multicanal:
        # las series reales viven en el ENCODER como canales; dejarlas ademas agregadas en los
        # estaticos seria escribir el mismo dato dos veces y con peor resolucion
        cols_est = [c for c in cols_est if not c.endswith(("_Dm1", "_Dm6"))]   # _Dm2 se queda
    else:
        cols_est += [c for c in df.columns if c.endswith(("_Dm1", "_Dm6"))]

    const = [c for c in cols_dec + cols_est if df[c].nunique(dropna=True) <= 1]
    cols_dec = [c for c in cols_dec if c not in const]
    cols_est = [c for c in cols_est if c not in const]
    if const:
        print(f"  constantes descartadas ({len(const)}): {const}")

    # ── paneles (dia, hora, canal) ────────────────────────────────────────────────────────
    dias = np.array(sorted(df.fecha_objetivo.unique()))
    n_dias = len(dias)

    def panel(cols):
        p = (df.pivot_table(index="fecha_objetivo", columns="hora", values=cols, aggfunc="mean")
               .reindex(dias))
        return np.stack([p[c].to_numpy(dtype="float32") for c in cols], axis=-1)

    PRECIO = (df.pivot_table(index="fecha_objetivo", columns="hora", values="target_price",
                             aggfunc="mean").reindex(dias).to_numpy(dtype="float32"))
    DEC = panel(cols_dec)
    EST = (df.groupby("fecha_objetivo")[cols_est].first().reindex(dias)
             .to_numpy(dtype="float32"))

    # Series reales: `*_Dm1` de la fila con fecha_objetivo = T es el dato del dia T-2, asi que
    # reindexando por esa fecha se recupera la serie horaria continua y con ella cualquier
    # ventana. Es lo que permite tener 1 CSV y no dos.
    nombres_enc = ["precio"]
    if encoder_multicanal and cols_dm1:
        h = df[["fecha_objetivo", "hora"] + cols_dm1].copy()
        h["fecha_objetivo"] = h["fecha_objetivo"] - pd.Timedelta(days=2)
        h = h.rename(columns={c: c[:-4] for c in cols_dm1})
        nm = [c[:-4] for c in cols_dm1]
        pv = (h.pivot_table(index="fecha_objetivo", columns="hora", values=nm, aggfunc="mean")
                .reindex(dias))
        REAL = np.stack([pv[c].to_numpy(dtype="float32") for c in nm], axis=-1)
        nombres_enc += nm
    else:
        REAL = None

    # ── ventanas ──────────────────────────────────────────────────────────────────────────
    V = ventana
    t_idx = np.arange(V + 1, n_dias)

    dias_dt = pd.to_datetime(dias)
    excl = np.zeros(n_dias, dtype=bool)
    for ini, fin in PERIODOS_EXCLUIDOS:
        excl |= (dias_dt >= ini) & (dias_dt <= fin)

    # Contiguidad: borrar dias del medio rompe la ventana. `PRECIO[a:b]` opera sobre los dias
    # DISPONIBLES, asi que sin esta comprobacion la fila posterior al hueco tomaria siete
    # jornadas NO consecutivas y nada lo advertiria -- entrenaria igual, solo rendiria peor.
    ok_v = np.array([(not excl[t - V - 1:t].any())
                     and (dias_dt[t - 1] - dias_dt[t - V - 1]).days == V
                     for t in t_idx])
    if (~ok_v).any():
        print(f"  {int((~ok_v).sum())} dias descartados por periodo anomalo o ventana no contigua")
    t_idx = t_idx[ok_v]

    X_enc = np.stack([
        np.concatenate(
            [PRECIO[t - V - 1:t - 1].reshape(24 * V, 1)]
            + ([REAL[t - V - 1:t - 1].reshape(24 * V, REAL.shape[-1])] if REAL is not None else []),
            axis=-1)
        for t in t_idx])
    X_dec, X_est, y = DEC[t_idx], EST[t_idx], PRECIO[t_idx]
    fechas = dias[t_idx]

    ok = ~np.isnan(y).any(1) & ~np.isnan(X_enc).any((1, 2))
    X_enc, X_dec, X_est, y, fechas = X_enc[ok], X_dec[ok], X_est[ok], y[ok], fechas[ok]
    X_dec, X_est = np.nan_to_num(X_dec), np.nan_to_num(X_est)

    n_eu = sum(1 for c in cols_dec if c.endswith(("_entsoe_D", "_omie_D"))
               or c.startswith("spread_es_"))
    n_met = sum(1 for c in nombres_enc if "_met_" in c or c.startswith(
        ("t2m_", "d2m_", "msl_", "wind10_", "wind100_", "wind_gust10_", "tcc_", "ssrd_", "tp_")))
    print(f"  X_enc {X_enc.shape} ({len(nombres_enc)} canales, {n_met} meteo) | "
          f"X_dec {X_dec.shape} ({n_eu} precios EU) | X_est {X_est.shape} | y {y.shape}")
    return dict(X_enc=X_enc, X_dec=X_dec, X_est=X_est, y=y, fechas=fechas,
                cols_dec=cols_dec, cols_est=cols_est, canales=nombres_enc)


class Escalador:
    def fit(self, x):
        ejes = tuple(range(x.ndim - 1))
        self.mu = x.mean(axis=ejes, keepdims=True)
        sd = x.std(axis=ejes, keepdims=True)
        self.sd = np.where(sd < 1e-8, 1.0, sd)      # nunca dividir por ~0
        return self
    def __call__(self, x):
        return np.clip((x - self.mu) / self.sd, -10, 10).astype("float32")


def preparar(d):
    f = pd.to_datetime(d["fechas"])
    tr, va = f <= TRAIN_END, (f > TRAIN_END) & (f <= VAL_END)
    te = f > VAL_END
    print(f"  train {tr.sum()} | val {va.sum()} | test {te.sum()} dias")

    def purgar(X, nombres, et):
        sd = X[tr].std(axis=tuple(range(X.ndim - 1)))
        malas = set(np.where(sd < 1e-8)[0].tolist())
        if malas:
            print(f"  [{et}] {len(malas)} sin varianza en train: "
                  f"{[nombres[i] for i in sorted(malas)][:6]}...")
        buenas = [i for i in range(X.shape[-1]) if i not in malas]
        return X[..., buenas], [nombres[i] for i in buenas]

    d["X_dec"], d["cols_dec"] = purgar(d["X_dec"], d["cols_dec"], "decoder")
    d["X_est"], d["cols_est"] = purgar(d["X_est"], d["cols_est"], "estaticos")
    i_pD = d["cols_dec"].index("es_esios_D")

    d.update(tr=tr, va=va, te=te, i_pD=i_pD)
    d["Xe"] = Escalador().fit(d["X_enc"][tr])(d["X_enc"])
    d["Xd"] = Escalador().fit(d["X_dec"][tr])(d["X_dec"])
    d["Xs"] = Escalador().fit(d["X_est"][tr])(d["X_est"])

    naive = d["X_dec"][:, :, i_pD]
    resid = d["y"] - naive
    mu_r, sd_r = float(resid[tr].mean()), float(resid[tr].std())
    d["yr"] = ((resid - mu_r) / sd_r).astype("float32")
    d["inv_r"] = lambda p, m: p * sd_r + mu_r + naive[m]
    d["naive"] = naive
    d["plano"] = np.concatenate([d["Xe"].reshape(len(d["Xe"]), -1),
                                 d["Xd"].reshape(len(d["Xd"]), -1), d["Xs"]], axis=1)
    print(f"  residuo train: sd {sd_r:.2f} (precio: sd {d['y'][tr].std():.2f}) | "
          f"matriz plana {d['plano'].shape}")
    return d


# ══════════════════════════════════════════════════════════════════════════════════════════
#  Metricas
# ══════════════════════════════════════════════════════════════════════════════════════════

def metricas(yt, yp):
    e = yp - yt
    den = (np.abs(yt) + np.abs(yp)) / 2      # sMAPE, no MAPE: el precio pasa por cero
    n = np.arange(len(yt))
    # captura de spread: fraccion del arbitraje perfecto que se obtiene cargando en la hora de
    # valle PREDICHA y descargando en la de pico PREDICHA. El MAE mide precision de precio;
    # esto mide valor economico, y no son lo mismo.
    cap = ((yt[n, yp.argmax(1)] - yt[n, yp.argmin(1)]) /
           np.maximum(yt.max(1) - yt.min(1), .1))
    return {"MAE": float(np.abs(e).mean()),
            "RMSE": float(np.sqrt((e ** 2).mean())),
            "sMAPE": float(100 * np.mean(np.abs(e) / np.maximum(den, 1e-3))),
            "captura_%": float(100 * cap.mean()),
            "pico_1h_%": float(100 * (np.abs(yt.argmax(1) - yp.argmax(1)) <= 1).mean())}


def tabla(res, ref="naive D"):
    t = pd.DataFrame(res).T.sort_values("MAE")
    base = t.loc[[i for i in t.index if i.startswith("naive")], "MAE"].min()
    t["vs_naive_%"] = (100 * (t["MAE"] / base - 1)).round(1)
    return t


# ══════════════════════════════════════════════════════════════════════════════════════════

def seq2seq(shapes, u=48, dr=0.25):
    """Encoder LSTM + decoder alimentado hora a hora con X_dec.

    El contexto repetido se CONCATENA con la prevision horaria; un RepeatVector a secas daria
    al decoder el mismo vector en las 24 horas, sin manera de saber que a las 14:00 entran 8 GW
    de solar. Es la diferencia con el seq2seq de traduccion y lo que justifica la arquitectura.
    El decoder es UNIDIRECCIONAL: bidireccional miraria horas futuras.
    """
    from tensorflow import keras
    from tensorflow.keras import layers
    ie = keras.Input(shape=shapes[0], name="hist")
    idc = keras.Input(shape=shapes[1], name="fut")
    ist = keras.Input(shape=(shapes[2],), name="est")
    enc = layers.Bidirectional(layers.LSTM(u, dropout=dr))(ie)       # bidireccional OK: pasado
    ctx = layers.Concatenate()([enc, layers.Dense(64, activation="relu")(ist)])
    dec = layers.Concatenate()([layers.RepeatVector(24)(ctx), idc])
    dec = layers.LSTM(u, return_sequences=True, dropout=dr)(dec)
    out = layers.Reshape((24,))(layers.TimeDistributed(layers.Dense(1))(dec))
    return keras.Model([ie, idc, ist], out, name="Seq2Seq")


def ejecutar(d, epochs, batch, solo_baselines=False):
    res, preds = {}, {}
    te, tr, va = d["te"], d["tr"], d["va"]
    y = d["y"]

    preds["naive D (precio de hoy)"] = d["naive"][te]
    res["naive D (precio de hoy)"] = metricas(y[te], d["naive"][te])

    if solo_baselines:
        return res, preds

    from tensorflow import keras
    from tensorflow.keras import layers
    import tensorflow as tf
    tf.random.set_seed(SEMILLA)

    def compilar(m, lr=1e-3):
        m.compile(optimizer=keras.optimizers.Adam(lr),
                  loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
        return m
    CB = lambda: [keras.callbacks.EarlyStopping("val_loss", patience=12,
                                                restore_best_weights=True),
                  keras.callbacks.ReduceLROnPlateau("val_loss", factor=.5, patience=5,
                                                    min_lr=1e-5)]
    ent = lambda m: {"hist": d["Xe"][m], "fut": d["Xd"][m], "est": d["Xs"][m]}

    mlp = compilar(keras.Sequential([
        layers.Input(shape=(d["plano"].shape[1],)),
        layers.Dense(256, activation="relu"), layers.Dropout(0.35),
        layers.Dense(128, activation="relu"), layers.Dropout(0.35),
        layers.Dense(24)], name="MLP"))
    mlp.fit(d["plano"][tr], d["yr"][tr], validation_data=(d["plano"][va], d["yr"][va]),
            epochs=epochs, batch_size=batch, callbacks=CB(), verbose=0)
    preds["MLP residuo"] = d["inv_r"](mlp.predict(d["plano"][te], verbose=0), te)
    res["MLP residuo"] = metricas(y[te], preds["MLP residuo"])
    print(f"  MLP residuo         MAE {res['MLP residuo']['MAE']:6.2f}")

    s2s = compilar(seq2seq((d["Xe"].shape[1:], d["Xd"].shape[1:], d["Xs"].shape[1])))
    s2s.fit(ent(tr), d["yr"][tr], validation_data=(ent(va), d["yr"][va]),
            epochs=epochs, batch_size=batch, callbacks=CB(), verbose=0)
    preds["Seq2Seq residuo"] = d["inv_r"](s2s.predict(ent(te), verbose=0), te)
    res["Seq2Seq residuo"] = metricas(y[te], preds["Seq2Seq residuo"])
    print(f"  Seq2Seq residuo     MAE {res['Seq2Seq residuo']['MAE']:6.2f}")

    try:
        import lightgbm as lgb
        pl = np.zeros((int(te.sum()), 24))
        for h in range(24):
            g = lgb.LGBMRegressor(n_estimators=800, learning_rate=0.03, objective="huber",
                                  verbose=-1, random_state=SEMILLA)
            g.fit(d["plano"][tr], d["yr"][tr][:, h],
                  eval_set=[(d["plano"][va], d["yr"][va][:, h])],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
            pl[:, h] = g.predict(d["plano"][te])
        preds["LightGBM residuo"] = d["inv_r"](pl, te)
        res["LightGBM residuo"] = metricas(y[te], preds["LightGBM residuo"])
        print(f"  LightGBM residuo    MAE {res['LightGBM residuo']['MAE']:6.2f}")
    except ImportError:
        print("  (lightgbm no instalado)")

    # ── reentreno final con train+val ─────────────────────────────────────────────────────
    # La arquitectura ya esta elegida (con validacion). Ahora se reaprovecha validacion como
    # datos de entrenamiento para que el modelo llegue lo mas cerca posible de test. Sin
    # EarlyStopping: se fija el numero de epocas al que paro la fase anterior.
    if REENTRENAR_CON_VAL:
        ep = len(s2s.history.history["loss"]) if hasattr(s2s, "history") else 30
        final = compilar(seq2seq((d["Xe"].shape[1:], d["Xd"].shape[1:], d["Xs"].shape[1])))
        m_all = tr | va
        final.fit(ent(m_all), d["yr"][m_all], epochs=ep, batch_size=batch, verbose=0)
        preds["Seq2Seq final (train+val)"] = d["inv_r"](final.predict(ent(te), verbose=0), te)
        res["Seq2Seq final (train+val)"] = metricas(y[te], preds["Seq2Seq final (train+val)"])
        print(f"  Seq2Seq final       MAE {res['Seq2Seq final (train+val)']['MAE']:6.2f}  "
              f"({int(m_all.sum())} dias, {ep} epocas fijas)")

    base = res["naive D (precio de hoy)"]["MAE"]
    buenos = [k for k, v in res.items() if v["MAE"] < base and not k.startswith("naive")]
    if len(buenos) > 1:
        preds["Ensemble"] = np.mean([preds[k] for k in buenos], axis=0)
        res["Ensemble"] = metricas(y[te], preds["Ensemble"])
        print(f"  Ensemble ({len(buenos)})       MAE {res['Ensemble']['MAE']:6.2f}")
    return res, preds


def walk_forward(d, epochs, batch, cortes=("2022-12-31", "2023-12-31", "2024-12-31")):
    """Validacion cruzada temporal: varias ventanas consecutivas en vez de una sola.

    Con 339 dias de validacion, el error estandar del MAE es de +-1,1 EUR/MWh: diferencias
    menores no son distinguibles del azar. Promediando tres pliegues se triplica la muestra
    efectiva sin tocar train ni test, y ademas se ve si el modelo es ESTABLE entre años o si
    2025 fue un caso raro -- que es justo la duda que abrio la brecha val/test del Seq2Seq
    (batia al naive por 15% en validacion y perdia por 10% en test).

    Cuesta un entrenamiento por pliegue, asi que conviene reservarlo para la decision final
    entre las dos o tres arquitecturas que sobrevivan, no para la busqueda de hiperparametros.
    """
    from tensorflow import keras
    f = pd.to_datetime(d["fechas"])
    filas = []
    for corte in cortes:
        m_tr = f <= corte
        m_va = (f > corte) & (f <= (pd.Timestamp(corte) + pd.DateOffset(years=1)))
        if m_va.sum() < 60:
            continue
        m = seq2seq((d["Xe"].shape[1:], d["Xd"].shape[1:], d["Xs"].shape[1]))
        m.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss=keras.losses.Huber(delta=1.0), metrics=["mae"])
        ent = lambda k: {"hist": d["Xe"][k], "fut": d["Xd"][k], "est": d["Xs"][k]}
        m.fit(ent(m_tr), d["yr"][m_tr], validation_data=(ent(m_va), d["yr"][m_va]),
              epochs=epochs, batch_size=batch, verbose=0,
              callbacks=[keras.callbacks.EarlyStopping("val_loss", patience=12,
                                                       restore_best_weights=True)])
        p = d["inv_r"](m.predict(ent(m_va), verbose=0), m_va)
        mm = metricas(d["y"][m_va], p)
        nv = metricas(d["y"][m_va], d["naive"][m_va])
        filas.append({"corte": corte, "dias_train": int(m_tr.sum()), "dias_val": int(m_va.sum()),
                      "MAE": round(mm["MAE"], 2), "MAE_naive": round(nv["MAE"], 2),
                      "vs_naive_%": round(100 * (mm["MAE"] / nv["MAE"] - 1), 1)})
        print(f"  {corte}: MAE {mm['MAE']:.2f} vs naive {nv['MAE']:.2f} "
              f"({filas[-1]['vs_naive_%']:+.1f}%)")
    t = pd.DataFrame(filas)
    if len(t):
        print(f"\n  media de pliegues: {t['vs_naive_%'].mean():+.1f}% "
              f"(desviacion {t['vs_naive_%'].std():.1f})")
    return t


def exportar(d, res, preds, carpeta, etiqueta):
    te, y = d["te"], d["y"]
    t = tabla(res)
    mejor = t.index[0]
    p, yt = preds[mejor], y[te]
    fm = pd.to_datetime(d["fechas"][te])
    n = np.arange(len(yt))

    diario = pd.DataFrame({
        "fecha": fm.date, "precio_real_medio": yt.mean(1).round(2),
        "precio_pred_medio": p.mean(1).round(2),
        "MAE_dia": np.abs(p - yt).mean(1).round(2),
        "hora_min_real": yt.argmin(1), "hora_min_pred": p.argmin(1),
        "hora_max_real": yt.argmax(1), "hora_max_pred": p.argmax(1),
        "spread_perfecto": (yt.max(1) - yt.min(1)).round(2),
        "spread_capturado": (yt[n, p.argmax(1)] - yt[n, p.argmin(1)]).round(2)})
    diario["captura_pct"] = (100 * diario.spread_capturado /
                             np.maximum(diario.spread_perfecto, .1)).round(1)
    diario.to_csv(carpeta / f"resultados_diarios_{etiqueta}.csv", index=False)
    t.round(3).to_csv(carpeta / f"comparativa_modelos_{etiqueta}.csv")
    return t, mejor, diario


def main():
    ap = argparse.ArgumentParser(description="Modelos de precio D+1 sobre el dataset horario")
    ap.add_argument("--variante", default="ambas",
                    help="'sin_ntc_prev', 'sin_2020' o 'ambas' (defecto)")
    ap.add_argument("--csv", default=None, help="Ruta a un CSV concreto (ignora --variante)")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--ventana", type=int, default=VENTANA_DIAS)
    ap.add_argument("--solo-baselines", action="store_true")
    ap.add_argument("--encoder-solo-precio", action="store_true",
                    help="Ablacion: encoder con 1 canal en vez de multicanal")
    ap.add_argument("--walk-forward", action="store_true",
                    help="Validacion cruzada temporal (3 pliegues) en vez de una sola ventana")
    ap.add_argument("--sin-reentreno-final", action="store_true",
                    help="No reentrenar con train+val tras elegir el modelo")
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()
    if args.sin_reentreno_final:
        globals()["REENTRENAR_CON_VAL"] = False

    carpeta = Path(args.salida) if args.salida else Path(__file__).parent
    if args.csv:
        rutas = [(Path(args.csv).stem, Path(args.csv))]
    else:
        vs = ["sin_ntc_prev", "sin_2020"] if args.variante == "ambas" else [args.variante]
        rutas = []
        for v in vs:
            c = sorted(carpeta.glob(f"dataset_horario*{v}_v[0-9][0-9].csv"))
            if not c:
                print(f"AVISO: no encuentro CSV de la variante '{v}'")
                continue
            rutas.append((v, c[-1]))
        if not rutas:
            raise SystemExit("Sin CSV. Lanza antes construir_dataset_maestro_sergio.py")

    resumen = {}
    for etiqueta, ruta in rutas:
        print(f"\n{'='*78}\nVARIANTE: {etiqueta}\n{'='*78}")
        d = preparar(construir(ruta, args.ventana,
                               encoder_multicanal=not args.encoder_solo_precio))
        if args.walk_forward:
            print("\nValidacion cruzada temporal:")
            wf = walk_forward(d, args.epochs, args.batch)
            wf.to_csv(carpeta / f"walk_forward_{etiqueta}.csv", index=False)
        res, preds = ejecutar(d, args.epochs, args.batch, args.solo_baselines)
        t, mejor, diario = exportar(d, res, preds, carpeta, etiqueta)
        print(f"\n{t.round(2).to_string()}")
        print(f"\n  mejor: {mejor}")
        print(f"  captura de spread {diario.captura_pct.mean():.1f}% | "
              f"hora de pico +-1h {100*(np.abs(diario.hora_max_real-diario.hora_max_pred)<=1).mean():.0f}%")
        resumen[etiqueta] = {"mejor": mejor, **{k: round(v, 2)
                                                for k, v in t.loc[mejor].items()},
                             "dias_train": int(d["tr"].sum())}

    if len(resumen) > 1:
        print(f"\n{'='*78}\nCOMPARATIVA ENTRE VARIANTES\n{'='*78}")
        print(pd.DataFrame(resumen).T.to_string())
        print("\nNota: la variante 'sin_2020' sacrifica dias de entrenamiento a cambio de 6")
        print("columnas redundantes con ree_ntc_* (que ya estan en el decoder y si tienen")
        print("historico completo). Si aun asi gana, las dos series NO son equivalentes y")
        print("conviene comprobar su correlacion en el periodo donde coexisten.")


if __name__ == "__main__":
    main()
