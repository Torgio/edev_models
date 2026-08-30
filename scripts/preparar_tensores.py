"""De la matriz en parquet a los tensores que consumen las redes.

Es la logica de las secciones 1 a 3 y 8c del notebook del RNN, extraida para que la puedan
usar tambien los scripts que barren varias matrices sin abrir Jupyter. El notebook no se
toca: esto es una copia deliberada, y si divergen manda el notebook.

QUE DEVUELVE
Un objeto con los tres bloques de entrada, el objetivo y las mascaras de split:

    X_enc  (dias, 168, canales)   ventana de 7 dias, hora a hora
    X_dec  (dias,  24, columnas)  lo que se sabe de D+1 al predecir
    X_est  (dias, columnas)       constante dentro del dia
    y      (dias, 24)             el precio de D+1

LA FRONTERA DE INFORMACION, que es lo que hace que esto valga algo. A las 11:00 del dia D
solo esta publicado hasta cierto punto, y los desfases estan MEDIDOS contra las tablas
fuente, no deducidos del nombre (ver `scripts/auditoria_frontera.py`):

    `_D`     describe el dia D      publicado a las 13:00-13:45 de D-1
    `_Dm1`   describe D-1
    `_Dm2`   describe D-2
    `_Dm6`   describe D-6
    `*_meteo`  es la PREVISION de D+1
    `d1_*`     calendario de D+1, determinista

Cada bloque se reindexa a la fecha que describe y luego se recorta con la misma aritmetica
que el precio, para no arrastrar un desfase distinto por bloque hasta el final -- que es
como se cuelan los errores de un dia.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
HORAS = list(range(24))
VENTANA_DIAS = 7
TRAIN_END, VAL_END = "2024-12-31", "2025-12-31"

CLAVES = ["fecha_pred", "fecha_objetivo", "hora", "target_price", "split"]
BANDERAS = ["imputado_apagon", "ventana_pisa_apagon"]


@dataclass
class Tensores:
    X_enc: np.ndarray
    X_dec: np.ndarray
    X_est: np.ndarray
    y: np.ndarray
    fechas: np.ndarray
    tr: np.ndarray
    va: np.ndarray
    te: np.ndarray
    canales: list
    cols_dec: list
    cols_est: list
    i_pD: int
    naive: np.ndarray          # precio del dia D en ESCALA ORIGINAL, sin escalar
    # Los escaladores NO se tiran. Un modelo guardado espera entradas estandarizadas, y sin
    # la media y la desviacion de train recibe numeros en otra escala y devuelve basura SIN
    # AVISAR. Son parte del modelo tanto como los pesos.
    esc: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def preprocesado(self):
        """Todo lo que hace falta para servir el modelo fuera de aqui.

        Incluye el orden de las columnas a proposito: el modelo aprendio que el canal 17 es
        la eolica programada, y si la matriz se regenera con otro orden seguira prediciendo
        -- con numeros plausibles y equivocados. Al cargar hay que comprobarlo.
        """
        return {"hash_matriz": self.meta.get("hash"), "matriz": self.meta.get("nombre"),
                "ventana_dias": int(self.X_enc.shape[1] // 24),
                "canales": self.canales, "cols_dec": self.cols_dec,
                "cols_est": self.cols_est, "i_pD": int(self.i_pD),
                "escaladores": {k: {"mu": v.mu.ravel().tolist(),
                                    "sd": v.sd.ravel().tolist()}
                                for k, v in self.esc.items()}}

    def ent(self, m):
        return {"hist": self.X_enc[m], "fut": self.X_dec[m], "est": self.X_est[m]}


class Escalador:
    """Estandariza y recorta a 10 sigmas: mas alla es cambio de regimen o atipico, y en
    ningun caso debe dominar el gradiente."""

    def fit(self, x):
        ejes = tuple(range(x.ndim - 1))
        self.mu = x.mean(axis=ejes, keepdims=True)
        sd = x.std(axis=ejes, keepdims=True)
        self.sd = np.where(sd < 1e-8, 1.0, sd)
        return self

    def __call__(self, x):
        return np.clip((x - self.mu) / self.sd, -10, 10).astype("float32")


def _clasificar(df, multicanal=True):
    cols_dec = ([c for c in df.columns if c.endswith(("_prev_mw", "_prev"))]
                + [c for c in df.columns if c.startswith("ree_ntc_")]
                + [c for c in df.columns if c == "es_esios_D"]
                + [c for c in df.columns if c.endswith(("_entsoe_D", "_omie_D"))]
                + [c for c in df.columns if c.startswith("spread_es_")]
                + [c for c in df.columns if c.endswith("_meteo")]
                + [c for c in df.columns
                   if c in ("meteo_es_forecast", "hora_sin", "hora_cos")])
    cols_dec = list(dict.fromkeys(cols_dec))

    # el bloque `_D` es HORARIO y describe el dia D: canal de encoder, no estatico
    cols_prog = [c for c in df.columns
                 if c.startswith(("pdbc_", "pbfli_", "bil_")) and c.endswith("_D")]
    cols_dm1 = [c for c in df.columns if c.endswith("_Dm1") and c != "es_esios_Dm1"]
    cols_dm2 = [c for c in df.columns if c.endswith("_Dm2")]

    cand = [c for c in df.columns
            if c.startswith(("capinst_", "capdisp_", "d1_"))
            or c in ("gas_mibgas", "co2_eua_dec", "gas_ttf_m1")
            or c.startswith(("pbf_publicado", "pbf_completo"))] + cols_dm2
    if multicanal:
        cand = [c for c in cand if not c.endswith(("_Dm1", "_Dm6"))]
    else:
        cand += [c for c in df.columns if c.endswith(("_Dm1", "_Dm6"))] + cols_prog
        cols_prog, cols_dm1 = [], []
    cand = [c for c in dict.fromkeys(cand) if c not in cols_dec + CLAVES + BANDERAS]

    # horaria o diaria: medido, no supuesto. Las horarias que caerian en estaticos se
    # agregan a MEDIA del dia, no con `.first()`, que se quedaria con la madrugada.
    nun = df.groupby("fecha_objetivo")[cand].nunique().mean()
    return cols_dec, cols_prog, cols_dm1, [c for c in cand if nun[c] <= 1.5], \
        [c for c in cand if nun[c] > 1.5]


def preparar(matriz="nucleo", ventana=VENTANA_DIAS, multicanal=True, verbose=True):
    ruta = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    try:
        df = pd.read_parquet(ruta)
    except Exception as e:
        # Los parquet escritos desde WSL (pyarrow 25) no los abre el pyarrow 19 de
        # Windows: "Repetition level histogram size mismatch". El CSV tiene lo mismo y lo
        # lee cualquiera, asi que sirve de red -- tarda unos segundos mas y ya.
        csv = ruta.with_suffix(".csv")
        if not csv.exists():
            raise
        if verbose:
            print(f"  (el parquet no se puede leer aqui: {type(e).__name__}; se usa el CSV)")
        df = pd.read_csv(csv, parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
    df = df.sort_values(["fecha_objetivo", "hora"]).reset_index(drop=True)
    import json
    meta = json.loads((ruta.with_suffix(".meta.json")).read_text(encoding="utf-8"))

    cols_dec, cols_prog, cols_dm1, cols_est, cols_est_media = _clasificar(df, multicanal)
    dias = np.array(sorted(df.fecha_objetivo.unique()))
    n_dias = len(dias)

    def _rellena(p):
        # el domingo del cambio de hora tiene 23 h; el tensor es rectangular por definicion
        return p.reindex(columns=HORAS).interpolate(axis=1, limit_direction="both")

    def panel(cols, desfase=0):
        h = df[["fecha_objetivo", "hora"] + cols]
        if desfase:
            h = h.assign(fecha_objetivo=h["fecha_objetivo"] - pd.Timedelta(days=desfase))
        p = (h.pivot_table(index="fecha_objetivo", columns="hora", values=cols,
                           aggfunc="mean").reindex(dias))
        return np.stack([_rellena(p[c]).to_numpy(dtype="float32") for c in cols], axis=-1)

    PRECIO = _rellena(df.pivot_table(index="fecha_objetivo", columns="hora",
                                     values="target_price", aggfunc="mean")
                        .reindex(dias)).to_numpy(dtype="float32")
    DEC = panel(cols_dec)
    EST = (df.groupby("fecha_objetivo")[cols_est].first().reindex(dias).to_numpy("float32")
           if cols_est else np.zeros((n_dias, 0), "float32"))
    if cols_est_media:
        EST = np.concatenate([EST, df.groupby("fecha_objetivo")[cols_est_media].mean()
                              .reindex(dias).to_numpy("float32")], axis=1)
    cols_est = cols_est + cols_est_media

    canales, BLOQUES = ["precio"], []
    if multicanal:
        if cols_prog:
            BLOQUES.append(panel(cols_prog, desfase=1))      # `_D` describe T-1
            canales += [c[:-2] + "@D" for c in cols_prog]
        if cols_dm1:
            # DESFASE 1, NO 2. Con 2, el indice d del panel contenia la generacion OBSERVADA
            # del propio dia d, y como el encoder llega hasta T-1 = dia D, el modelo recibia
            # las 24 horas de generacion del dia en que se predice. A las 11:00 de D esa
            # generacion no existe: medido el 30/08/2026, `esios_gen` y `entsoe_gen_data`
            # tenian 0 horas del dia D. Era fuga, y ademas obligaba a que la matriz tuviera
            # dos dias mas alla del objetivo, que es lo que impedia predecir manana.
            #
            # Con desfase 1 el indice d lleva la generacion de d-1, asi que la ultima
            # posicion del encoder es D-1, que si esta publicada. El precio y el bloque
            # programado (`_D`, el PBF) se quedan como estaban: esos si existen a las 11:00.
            BLOQUES.append(panel(cols_dm1, desfase=1))       # `_Dm1` -> el dia anterior
            canales += [c[:-4] for c in cols_dm1]

    V = ventana
    t_idx = np.arange(V + 1, n_dias)
    dias_dt = pd.to_datetime(dias)
    # reindexar con desfase k deja los ultimos k dias vacios: se descartan sus ventanas
    sin_dato = np.isnan(PRECIO).any(axis=1)
    for b in BLOQUES:
        sin_dato |= np.isnan(b).any(axis=(1, 2))
    ok = np.array([(not sin_dato[t - V:t].any())
                   and (dias_dt[t - 1] - dias_dt[t - V - 1]).days == V for t in t_idx])
    t_idx = t_idx[ok]

    X_enc = np.stack([np.concatenate(
        [PRECIO[t - V:t].reshape(24 * V, 1)]
        + [b[t - V:t].reshape(24 * V, b.shape[-1]) for b in BLOQUES], axis=-1)
        for t in t_idx])
    X_dec, X_est, y = DEC[t_idx], EST[t_idx], PRECIO[t_idx]
    fechas = dias[t_idx]
    assert not np.isnan(X_enc).any(), "NaN en el encoder: revisa el desfase de algun bloque"

    f = pd.to_datetime(fechas)
    tr, va, te = (f <= TRAIN_END), (f > TRAIN_END) & (f <= VAL_END), (f > VAL_END)

    def purgar(X, nombres):
        sd = X[tr].std(axis=tuple(range(X.ndim - 1)))
        b = [i for i in range(X.shape[-1]) if sd[i] >= 1e-8]
        return X[..., b], [nombres[i] for i in b]

    X_dec, cols_dec = purgar(X_dec, cols_dec)
    X_est, cols_est = purgar(X_est, cols_est)
    i_pD = cols_dec.index("es_esios_D")
    # El naive se toma ANTES de escalar: es una prediccion en EUR/MWh, no una feature.
    # Tomarlo de `Xd` daria el precio estandarizado y el residuo no tendria sentido fisico.
    naive = X_dec[:, :, i_pD].copy()

    s_enc, s_dec, s_est = (Escalador().fit(X_enc[tr]), Escalador().fit(X_dec[tr]),
                           Escalador().fit(X_est[tr]))
    Xe, Xd, Xs = s_enc(X_enc), s_dec(X_dec), s_est(X_est)

    if verbose:
        print(f"{matriz:9s} hash {meta.get('hash','?')} · {len(fechas):,} dias · "
              f"encoder {Xe.shape[-1]} canales · decoder {Xd.shape[-1]} · "
              f"estaticos {Xs.shape[-1]} · train {int(tr.sum())} val {int(va.sum())} "
              f"test {int(te.sum())}")
    return Tensores(Xe, Xd, Xs, y, fechas, tr, va, te, canales, cols_dec, cols_est,
                    i_pD, naive, {"enc": s_enc, "dec": s_dec, "est": s_est}, meta)


def residuo(T: Tensores):
    """Objetivo en forma de residuo frente al naive, que es lo que hace que esto funcione.

    En escala absoluta ningun modelo bate a la persistencia: la red tira hacia la media del
    target que ha visto y el nivel de test esta medio sigma por debajo. Prediciendo la
    diferencia contra el naive, el nivel deja de ser un problema.
    """
    resid = T.y - T.naive
    mu, sd = float(resid[T.tr].mean()), float(resid[T.tr].std())
    yr = ((resid - mu) / sd).astype("float32")
    inv = lambda p, m: p * sd + mu + T.naive[m]
    return yr, inv, mu, sd
