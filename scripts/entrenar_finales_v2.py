"""Fase B v2: las 8 familias de red, los arboles de Willy y los clasicos de Samuel, sin Trayport.

QUE ANADE A entrenar_finales.py
Las 8 familias se entrenan exactamente igual: sus funciones se importan de
`entrenar_finales`, no se copian. Lo nuevo son las familias que el equipo entreno fuera de
ese arnes, rehechas aqui sobre la MISMA matriz, el MISMO corte y la MISMA funcion
`metricas`, para que la tabla compare modelos y no condiciones:

    familia       autor    tipo          entrada                      objetivo  semillas
    lgbm_nucleo   Willy    arbol         matriz plana, fila por hora  precio    3
    ridge         Samuel   lineal        plana + filtro Spearman      precio    1
    elasticnet    Samuel   lineal        plana + filtro Spearman      precio    1
    sarima        Samuel   estadistico   serie horaria del precio     precio    1
    sarimax       Samuel   estadistico   serie + exogenas Spearman    precio    1

Los lineales y los SARIMA son deterministas: tres semillas darian tres veces el mismo modelo
y una desviacion de 0,000 que no mide nada -- el mismo artefacto que tuvo `boosting`.

SIN TRAYPORT
`co2_eua_dec` y `gas_ttf_m1` eran vistas sobre `trayport_daily_ohlc`, fuente que el equipo
retiro (nota 54). Se quitan de la matriz ANTES de construir cualquier entrada -- tensores y
matriz plana -- y cada lista de features se comprueba contra el veto antes de entrenar. Los
resultados de `finales_nucleo` del 30-ago se entrenaron CON ellas (lo dicen sus
`.preprocesado.json`), asi que no se reutilizan: todo sale en `finales_v2_<matriz>/`.

LA COPIA DEL DIA D
Las curvas predichas para D+1 se parecian demasiado a la de D. No es fuga ni un fallo de codigo:
7 de las 8 redes predicen el residuo contra el precio de D, y lo que no saben explicar lo dejan
en cero -- es decir, en D. Medido sobre el ensemble de `finales_nucleo` en test: el perfil
horario predicho correla 0,948 con el de D cuando el real solo correla 0,858, y la prediccion se
aparta de D un 77 % de lo que se aparta la realidad (70 % en el cuartil de dias con mas cambio;
el lunes, con D en domingo, es el peor dia). Dos cambios:

  ancla semanal   el residuo se toma contra D corregido con D-6, que cae en el MISMO dia de la
                  semana que D+1: ancla = D + w[dia de la semana de D+1, hora] * (D-6 - D), con
                  w en [0, 1] ajustado solo en train. Sola, como referencia, baja el MAE del
                  naive de 19,95 a 18,84 en validacion y de 16,72 a 16,32 en test.
                  PROBADA Y DESCARTADA como valor por defecto: con las 13 familias reduce algo
                  la copia (perfil vs D 0,93 frente a 0,95) pero no mejora la validacion, que es
                  el criterio (ensemble seleccionado 11,83 frente a 11,73 con ancla D; el de 8
                  familias 12,68 frente a 12,52). Queda disponible con `--ancla semanal`.
  medir la copia  cada modelo lleva `movimiento_%`, `corr_cambio` y `forma_vs_D` en test, para
                  comprobar si se sigue pegando a D en vez de suponerlo (ver `copia`).

LA FRONTERA EN PRODUCCION
A las 11:00 de D el modelo solo tendra previsiones de D+1 y datos ya publicados; entrenarlo con
otra cosa es medir un error que en produccion no existe.
  met_lags   `*_met_Dm1` y `*_met_Dm2` salen de ERA5, reanalisis con unos 5 dias de retraso: a
             las 11:00 no existe el de D-1. Solo los usan los modelos planos (el tensor no los
             lleva). Vetados por defecto mientras la matriz no se reconstruya con
             ERA5_PREFERIR_ECMWF; despues, `--vetar` sin argumentos los devuelve.
  ree_prev   `ree_*_prev` son previsiones de REE para D+1, pero el historico se cargo con los
             valores ya revisados (constructor_base, pendiente [5]). No se vetan por defecto:
             `--vetar met_lags ree_prev` mide cuanto depende el modelo de ellas.
Los precios y programas `_D` son legitimos: el precio de D se caso a las 13:00 de D-1 y el PBF a
las 13:45. `scripts/auditoria_frontera.py` lo mide contra las tablas fuente.

EVALUACION COMUN
Cada familia devuelve (dias, 24) en EUR/MWh alineado con `T.fechas`, y todo se mide contra
`T.y`. La matriz es de reloj local: `ts` sin zona == fecha_objetivo + hora, 24 filas por dia
salvo el domingo de marzo, que no tiene hora 2. Los modelos planos la rellenan igual que
`modelos_equipo.ArbolPlano` (interpolando dentro del dia), y SARIMA recorre esa misma
rejilla de 24 horas por dia, asi que sus bloques de 24 caen exactamente en los dias objetivo.

Uso (en WSL: GPU para las redes, statsmodels y pmdarima para SARIMA). TensorFlow no encuentra
las librerias CUDA de los paquetes pip `nvidia-*` si no estan en LD_LIBRARY_PATH: sin esta
linea dice "Skipping registering GPU devices" y entrena en CPU sin avisar de otra forma.
    export LD_LIBRARY_PATH=$(python -c "import glob,site; print(':'.join(glob.glob(site.getsitepackages()[0] + '/nvidia/*/lib')))"):/usr/lib/wsl/lib
    python scripts/entrenar_finales_v2.py                          # todo, 3 semillas
    python scripts/entrenar_finales_v2.py --guardar-modelos
    python scripts/entrenar_finales_v2.py --familias lgbm_nucleo ridge elasticnet
    python scripts/entrenar_finales_v2.py --familias sarima sarimax --rebuscar-orden
    python scripts/entrenar_finales_v2.py --prueba                 # humo: minutos, carpeta *_prueba
    python scripts/entrenar_finales_v2.py --ancla semanal          # residuo contra D corregido con D-6 (descartada)
    python scripts/entrenar_finales_v2.py --vetar met_lags ree_prev   # ablacion de las previsiones REE
Cada configuracion distinta de la de por defecto escribe en su propia carpeta
(`finales_v2_nucleo_anclasemanal`, `..._veto-met_lags+ree_prev`), para que el resume no las mezcle.

EL MODELO FINAL es `ensemble_seleccion`: seleccion voraz de Caruana et al. (2004) sobre
validacion entre el mejor representante de cada familia (ver `_ensemble_seleccion`). Sustituye
a la regla "bate al naive en validacion", que dejaba entrar a ridge y sarimax, peores que la
persistencia en test. `ensemble` y `ensemble_todos` se siguen calculando como referencia.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))

import entrenar_finales as EF  # noqa: E402
from preparar_tensores import TRAIN_END  # noqa: E402

SEMILLA = EF.SEMILLA
REDES = list(EF.FAMILIAS)                  # las 8 de 06, `boosting` incluido
NUEVAS = ["lgbm_nucleo", "ridge", "elasticnet", "sarima", "sarimax"]
FAMILIAS = REDES + NUEVAS
DETERMINISTAS = {"ridge", "elasticnet", "sarima", "sarimax"}

AUTOR = {**{f: "Torgio" for f in REDES}, "lgbm_nucleo": "Willy",
         "ridge": "Samuel", "elasticnet": "Samuel", "sarima": "Samuel", "sarimax": "Samuel"}
TIPO = {**{f: "red" for f in REDES}, "boosting": "arbol", "lgbm_nucleo": "arbol",
        "ridge": "lineal", "elasticnet": "lineal", "sarima": "estadistico", "sarimax": "estadistico"}

# `trayport_daily_ohlc` alimentaba estas dos columnas (scripts/constructor_base.py). Las de
# prefijo `commodities_` son sus nombres en la capa de depuracion.
TRAYPORT = ("co2_eua_dec", "gas_ttf_m1", "commodities_co2_eua_dec", "commodities_gas_ttf_m1")


# --- Frontera en produccion -------------------------------------------------------------------
def _es_met_lag(c) -> bool:
    """`*_met_Dm1` / `*_met_Dm2`: ERA5, con ~5 dias de retraso. A las 11:00 de D no existe."""
    return "_met_Dm" in str(c)


def _es_ree_prev(c) -> bool:
    """`ree_*_prev`: prevision de REE para D+1, con historico cargado ya revisado."""
    return str(c).startswith("ree_") and str(c).endswith("_prev")


VETOS = {"met_lags": _es_met_lag, "ree_prev": _es_ree_prev}
VETOS_DEFECTO = ("met_lags",)
ANCLAS = ("semanal", "D")
ANCLA_DEFECTO = "D"            # la semanal se probo y no mejora la validacion (ver docstring)
COLS_COPIA = ("movimiento_test_%", "corr_cambio_test", "forma_vs_D_test")

# Que dia describe cada columna segun su NOMBRE. Es la etiqueta, no la medida -- esa la hace
# scripts/auditoria_frontera.py contra las tablas fuente --, pero deja ver de un vistazo que
# consume cada modelo. Gana la primera regla que case: `_met_Dm1` tambien acaba en `_Dm1`.
FRONTERA = (
    ("D+1 calendario", lambda c: c.startswith("d1_") or c in ("hora", "hora_sin", "hora_cos")),
    ("D+1 prevision meteo", lambda c: c.endswith("_meteo")),
    ("D+1 prevision REE", _es_ree_prev),
    ("D+1 declaracion previa", lambda c: c.startswith("capdisp_")),
    ("D-1/D-2 reanalisis ERA5", _es_met_lag),
    ("D casado ayer", lambda c: c.endswith("_D")),
    ("D-1", lambda c: c.endswith("_Dm1")),
    ("D-2", lambda c: c.endswith("_Dm2")),
    ("D-6", lambda c: c.endswith("_Dm6")),
)

# --- Willy ----------------------------------------------------------------------------------
# Ganadores de su Optuna (300 pruebas sobre su matriz horaria, notebook 06 seccion 6.1). Son los
# de modelos/lightgbm_nucleo_export/metadata.json en main y en willy_test; se copian aqui porque
# esa metadata cambia de rama a rama (122 features con Trayport, 120 sin el) y estos numeros no.
# No se re-afinan sobre nucleo, igual que hizo el.
LGBM_WILLY = dict(n_estimators=1900, num_leaves=27, max_depth=10,
                  learning_rate=0.017265119138779546, subsample=0.597032055679207,
                  colsample_bytree=0.7696132252880693, reg_alpha=0.0010694942634823176,
                  reg_lambda=0.0228121782480138, min_child_samples=8)
# Su regla de columnas (modelos/modelo_lightgbm_nucleo.py): todo menos control, banderas de
# trazabilidad, baterias con arranque tardio y objetivo. Aqui, ademas, Trayport.
NO_FEATURES_WILLY = (
    "fecha_pred", "fecha_objetivo", "ts", "split", "hora",
    "meteo_es_forecast", "imputado_apagon", "ventana_pisa_apagon",
    "pbf_publicado_D", "pbf_completo_D",
    "capinst_battery_hybrid_mw", "capinst_solar_pv_hybrid_mw", "capinst_wind_hybrid_mw",
    "ree_cbattery_mw_Dm1", "ree_cbattery_mw_Dm6", "ree_gbattery_mw_Dm1", "ree_gbattery_mw_Dm6",
    "target_price")

# --- Samuel ---------------------------------------------------------------------------------
# Su `data.preparar_xy`: fuera solo el control de la fila; `hora` se queda como feature.
NO_FEATURES_SAMUEL = ("fecha_pred", "fecha_objetivo", "ts", "split", "target_price")
# Ordenes que encontro su auto_arima (modelos/ML_Samuel/salidas/artifacts/orden_*.pkl). Buscarlos
# con m=24 cuesta horas; --rebuscar-orden lo repite con su misma funcion sobre los ultimos 120
# dias de train. El de SARIMAX no tiene parte ARIMA: con ~100 exogenas auto_arima prefirio una
# regresion pura, y asi se reproduce.
ORDEN_SARIMA = ((3, 1, 0), (1, 0, 0, 24))
ORDEN_SARIMAX = ((0, 0, 0), (0, 0, 0, 24))
# Su rejilla de Ridge acaba en alpha=500, y en el run completo sobre nucleo el mejor fue justo
# ese borde, con el MAE de validacion todavia bajando: se amplia hacia arriba.
RIDGE_ALPHAS_EXTRA = (1000, 2000, 5000, 10000, 20000)

_SPEARMAN: dict = {}
_VETADAS: set = set()          # columnas de los vetos de frontera activos en esta ejecucion


def carpeta_salida(matriz="nucleo", prueba=False, ancla=ANCLA_DEFECTO, vetos=VETOS_DEFECTO) -> Path:
    """`finales_v2_<matriz>` para la configuracion por defecto; cualquier otra, en carpeta propia."""
    partes = [f"finales_v2_{matriz}"]
    if ancla != ANCLA_DEFECTO:
        partes.append(f"ancla{ancla}")
    vetos = tuple(sorted(set(vetos)))
    if vetos != VETOS_DEFECTO:
        partes.append("veto-" + ("+".join(vetos) or "ninguno"))
    if prueba:
        partes.append("prueba")
    return REPO / "data" / "gold" / "_".join(partes)


def _samuel():
    """Su paquete `tfm_horario`, bajo demanda: arrastra statsmodels y pmdarima."""
    ruta = str(REPO / "modelos" / "ML_Samuel")
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
    from tfm_horario import ajustes, selection
    from tfm_horario.models import sarima
    return ajustes, selection, sarima


# ── Trayport ──────────────────────────────────────────────────────────────────────────────
def es_trayport(col) -> bool:
    return col in TRAYPORT or "trayport" in str(col).lower()


def vetar(features, quien) -> list:
    """Para en seco si una lista de features trae algo de Trayport o de un veto de frontera."""
    malas = [c for c in features if es_trayport(c) or c in _VETADAS]
    if malas:
        raise RuntimeError(f"{quien}: features vetadas {malas} (Trayport o frontera), "
                           "no deberian haber pasado el filtro")
    return list(features)


def columnas_matriz(matriz="nucleo") -> list:
    ruta = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    try:
        import pyarrow.parquet as pq
        return pq.read_schema(ruta).names
    except Exception:
        return pd.read_csv(ruta.with_suffix(".csv"), nrows=0).columns.tolist()


def comprobar_trayport(matriz="nucleo") -> dict:
    """Que columnas de Trayport trae la matriz en disco, antes de quitarlas."""
    return {"vetadas": list(TRAYPORT),
            "presentes": [c for c in columnas_matriz(matriz) if es_trayport(c)]}


def columnas_vetadas(matriz="nucleo", vetos=VETOS_DEFECTO) -> list:
    """Las columnas de la matriz que caen en algun veto de frontera."""
    return [c for c in columnas_matriz(matriz) if any(VETOS[v](c) for v in vetos)]


def frontera(cols) -> dict:
    """Columnas agrupadas por el dia que describen (regla de nombre, ver FRONTERA)."""
    grupos: dict = {}
    for c in cols:
        g = next((n for n, regla in FRONTERA if regla(c)), "sin dia (estructural o estatica)")
        grupos.setdefault(g, []).append(c)
    return grupos


# ── Matriz plana ──────────────────────────────────────────────────────────────────────────
def cargar_plana(matriz="nucleo", excluir=()) -> pd.DataFrame:
    """La matriz indexada por (fecha_objetivo, hora), sin Trayport ni las columnas de `excluir`.

    Misma lectura que `modelos_equipo.matriz_plana` (parquet, o el CSV si el pyarrow local no
    abre el parquet), sin su cache: aqui se lee una vez por ejecucion.
    """
    ruta = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    try:
        df = pd.read_parquet(ruta)
    except Exception:
        df = pd.read_csv(ruta.with_suffix(".csv"), parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
    df = df.drop(columns=[c for c in df.columns if es_trayport(c) or c in excluir])
    df = df.set_index([pd.to_datetime(df.fecha_objetivo), df.hora.astype(int)])
    df.index.names = ["fecha_objetivo", "hora"]
    if df.index.has_duplicates:
        df = df.groupby(level=[0, 1]).mean(numeric_only=True)
    return df


def filas(P, fechas, cols) -> pd.DataFrame:
    """Las 24 filas de cada dia, en orden dia-mayor. La regla de `ArbolPlano.filas`: el domingo
    de marzo no tiene hora 2 y se interpola dentro del dia; un dia entero ausente para."""
    idx = pd.MultiIndex.from_product([pd.to_datetime(fechas), range(24)],
                                     names=["fecha_objetivo", "hora"])
    X = P.reindex(idx)[list(cols)].astype("float64")
    hueco = X.isna().any(axis=1)
    if hueco.any():
        dias = X.index.get_level_values(0)[hueco].unique()
        X.loc[dias] = (X.loc[dias].groupby(level=0, group_keys=False)
                       .apply(lambda g: g.interpolate(limit_direction="both")))
    if X.isna().any().any():
        faltan = X.index[X.isna().any(axis=1)]
        raise ValueError(f"{len(faltan)} filas sin dato en la matriz, la primera {faltan[0]}")
    return X


def _train(P) -> np.ndarray:
    """Filas de entrenamiento: todas las horas con fecha objetivo hasta TRAIN_END, el mismo
    corte que el tensor."""
    return np.asarray(P.index.get_level_values(0) <= pd.Timestamp(TRAIN_END))


def numericas(P, excluir) -> list:
    return [c for c in P.columns if c not in excluir and pd.api.types.is_numeric_dtype(P[c])]


# ── Willy ─────────────────────────────────────────────────────────────────────────────────
def entrenar_lgbm_nucleo(T, P, semilla, prueba=False):
    """Un LightGBM para las 24 horas, sobre filas horarias y precio absoluto.

    Distinto de `boosting` en las dos cosas que Willy midio: un solo modelo que comparte lo
    aprendido entre horas en vez de 24 separados, y la matriz fila a fila en vez de la vista
    resumida del encoder.
    """
    import lightgbm as lgb
    cols = vetar(numericas(P, NO_FEATURES_WILLY), "lgbm_nucleo")
    tr = _train(P)
    params = dict(LGBM_WILLY, subsample_freq=1, random_state=semilla, n_jobs=-1, verbosity=-1)
    if prueba:
        params["n_estimators"] = 60
    g = lgb.LGBMRegressor(**params)
    g.fit(P.loc[tr, cols].astype("float64"), P.loc[tr, "target_price"])
    pv = g.predict(filas(P, T.fechas[T.va], cols)).reshape(-1, 24)
    pt = g.predict(filas(P, T.fechas[T.te], cols)).reshape(-1, 24)
    return pv, pt, int(g.booster_.num_trees()), g, {"features": cols, "hiperparametros": params}


# ── Samuel ────────────────────────────────────────────────────────────────────────────────
def features_samuel(P) -> list:
    """Su filtro de Spearman, misma funcion y umbrales, ajustado solo con train. Una vez por
    ejecucion y conjunto de candidatas: lo comparten ridge, elasticnet y sarimax, y un veto
    distinto en la misma sesion (el notebook) no reutiliza una seleccion que ya no vale."""
    cand = vetar(numericas(P, NO_FEATURES_SAMUEL), "spearman")
    clave = tuple(cand)
    if clave not in _SPEARMAN:
        _, selection, _ = _samuel()
        tr = _train(P)
        r = selection.select_features_spearman(P.loc[tr, cand], P.loc[tr, "target_price"])
        _SPEARMAN[clave] = vetar(r["selected"], "spearman")
        print(f"   spearman: {len(_SPEARMAN[clave])} de {len(cand)} features")
    return _SPEARMAN[clave]


def entrenar_lineal(fam, T, P, prueba=False):
    """Ridge o ElasticNet como en `tfm_horario.models`: rejilla elegida por MAE de validacion
    sobre X tipificadas y modelo final con el StandardScaler DENTRO del pipeline.

    El hiperparametro se elige mirando validacion, asi que esa metrica queda algo optimista;
    el test no se toca.
    """
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import ElasticNet, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    aj, _, _ = _samuel()
    cols = features_samuel(P)
    tr = _train(P)
    Xtr, ytr = P.loc[tr, cols].astype("float64"), P.loc[tr, "target_price"]
    Xva, yva = filas(P, T.fechas[T.va], cols), T.y[T.va].ravel()
    esc = StandardScaler().fit(Xtr)
    Xtr_s, Xva_s = esc.transform(Xtr), esc.transform(Xva)

    if fam == "ridge":
        rejilla = [{"alpha": a} for a in ([1, 100] if prueba else (*aj.RIDGE_ALPHAS, *RIDGE_ALPHAS_EXTRA))]

        def hacer(p):
            return Ridge(**p)
    else:
        iters = 5000 if prueba else aj.EN_MAX_ITER
        rejilla = [{"alpha": a, "l1_ratio": r}
                   for a in ([0.1, 1] if prueba else aj.EN_ALPHAS)
                   for r in ([0.5, 1.0] if prueba else aj.EN_L1_RATIOS)]

        def hacer(p):
            return ElasticNet(**p, max_iter=iters)

    def ajustar(estimador, X, y) -> bool:
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always", ConvergenceWarning)
            estimador.fit(X, y)
        return not any(issubclass(a.category, ConvergenceWarning) for a in avisos)

    tabla = []
    for p in rejilla:
        m = hacer(p)
        convergio = ajustar(m, Xtr_s, ytr)
        tabla.append({**p, "MAE_val": float(np.abs(m.predict(Xva_s) - yva).mean()),
                      "convergio": convergio})
    tabla = pd.DataFrame(tabla).sort_values("MAE_val").reset_index(drop=True)
    # Samuel elige solo entre las que convergieron: una a medias no es reproducible.
    elegibles = tabla[tabla.convergio] if tabla.convergio.any() else tabla
    mejor = {k: float(elegibles.iloc[0][k]) for k in rejilla[0]}
    for k, v in mejor.items():
        valores = sorted({p[k] for p in rejilla})
        # l1_ratio = 1 es Lasso puro, un limite natural y no un borde que haya que ampliar
        if len(valores) > 1 and v in (valores[0], valores[-1]) and not (k == "l1_ratio" and v == 1.0):
            print(f"   AVISO {fam}: {k}={v:g} cae en el borde de la rejilla "
                  f"[{valores[0]:g}, {valores[-1]:g}]; el optimo puede estar fuera")

    final = Pipeline([("escalado", StandardScaler()), ("modelo", hacer(mejor))])
    if not ajustar(final, Xtr, ytr):
        print(f"   AVISO {fam}: el ajuste final no convergio con {mejor}; no es reproducible")
    pv = final.predict(Xva).reshape(-1, 24)
    pt = final.predict(filas(P, T.fechas[T.te], cols)).reshape(-1, 24)
    return pv, pt, len(cols), final, {"features": cols, "hiperparametros": mejor, "rejilla": tabla}


def serie_reloj(P, cols=()):
    """La matriz como serie horaria continua de 24 horas por dia.

    Es la rejilla de `ts` de Samuel, pero construida desde (fecha_objetivo, hora) como el
    tensor: el domingo de marzo recibe su hora 2 interpolada, y cada bloque de 24 horas es
    exactamente un dia objetivo.
    """
    f = P.index.get_level_values(0)
    dias = pd.date_range(f.min(), f.max(), freq="D")
    X = filas(P, dias, ["target_price", *cols])
    idx = pd.date_range(dias[0], periods=len(X), freq="h")
    y = pd.Series(X["target_price"].to_numpy(), index=idx, name="target_price")
    exog = pd.DataFrame(X[list(cols)].to_numpy(), index=idx, columns=list(cols)) if cols else None
    return y, exog


def entrenar_sarima(fam, T, P, rebuscar=False, prueba=False):
    """SARIMA o SARIMAX con el motor de Samuel (`tfm_horario.models.sarima`).

    Ajuste sobre train con `low_memory` y estado reconstruido en los ultimos 30 dias, y
    prediccion `bloques24`: cada dia objetivo se predice entero con informacion hasta el dia
    anterior y despues se le da al modelo el dia observado -- lo que se sabe a las 11:00, porque
    el precio de D se caso ayer. La serie se recorre de un tiron desde enero de 2025 hasta el
    final de test (el estado no puede saltarse dias) y solo se miden los dias de `T`.
    """
    _, _, motor = _samuel()
    cols = features_samuel(P) if fam == "sarimax" else []
    y, X = serie_reloj(P, cols)
    n_tr = int((y.index < pd.Timestamp(TRAIN_END) + pd.Timedelta(days=1)).sum())
    y_tr, y_ob = y.iloc[:n_tr], y.iloc[n_tr:]
    X_tr = X.iloc[:n_tr] if X is not None else None
    X_ob = X.iloc[n_tr:] if X is not None else None
    if prueba:
        # humo: los 5 primeros dias de validacion. SARIMAX necesita un ano de ajuste: con 60 dias
        # y 65 exogenas la regresion esta mal condicionada y diverge tipifique o no.
        dias = 365 if X is not None else 60
        y_tr, y_ob = y_tr.iloc[-24 * dias:], y_ob.iloc[:24 * 5]
        if X is not None:
            X_tr, X_ob = X_tr.iloc[-24 * dias:], X_ob.iloc[:24 * 5]

    escalado = None
    if X is not None:
        # Tipificadas con la media y la desviacion de la ventana de ajuste. En crudo (MW junto a
        # fracciones y senos) el optimizador de statsmodels no llega: con un ano de ajuste, 71,6
        # de MAE en 5 dias de validacion en crudo y 28,5 tipificadas. Las constantes en esa
        # ventana se quitan: con desviacion 0 no hay coeficiente que estimar.
        mu, sd = X_tr.mean(), X_tr.std()
        vivas = sd.index[sd > 1e-8].tolist()
        X_tr = (X_tr[vivas] - mu[vivas]) / sd[vivas]
        X_ob = (X_ob[vivas] - mu[vivas]) / sd[vivas]
        escalado = {"mu": mu[vivas].to_dict(), "sd": sd[vivas].to_dict(),
                    "constantes_fuera": [c for c in cols if c not in vivas]}
        cols = vivas

    if rebuscar:
        orden, estacional = motor.buscar_orden(y_tr, X_tr)
    else:
        orden, estacional = ORDEN_SARIMAX if fam == "sarimax" else ORDEN_SARIMA
    if X is not None and orden[1] == 0 and estacional[1] == 0:
        # Sin diferenciar, SARIMAX(0,0,0) es una regresion SIN termino independiente, y con las
        # exogenas tipificadas (media 0) no puede representar el nivel del precio: en el run
        # completo predijo con un sesgo de -80 EUR/MWh en validacion (MAE 80,3; 14,2 quitando
        # el sesgo). La columna constante le devuelve el intercepto.
        X_tr, X_ob = X_tr.assign(constante=1.0), X_ob.assign(constante=1.0)
        escalado["constante"] = True
    t0 = time.time()
    fit = motor.ajustar(y_tr, orden, estacional, exog=X_tr)
    pred = motor.predecir(fit, y_ob, exog_objetivo=X_ob, estrategia="bloques24")
    print(f"   {fam}: SARIMAX{tuple(orden)}{tuple(estacional)} · {len(cols)} exogenas · "
          f"{(time.time() - t0) / 60:.1f} min")

    dias = pd.date_range(y_ob.index[0], periods=len(pred) // 24, freq="D")
    tabla = pd.DataFrame(pred.to_numpy().reshape(-1, 24), index=dias)
    pv = tabla.reindex(pd.to_datetime(T.fechas[T.va])).to_numpy()
    pt = tabla.reindex(pd.to_datetime(T.fechas[T.te])).to_numpy()
    extra = {"features": cols, "orden": [list(orden), list(estacional)]}
    if escalado:
        extra["escalado_exog"] = escalado
    return pv, pt, int(len(fit.params)), fit, extra


# ── La copia del dia D ────────────────────────────────────────────────────────────────────
def ancla_semanal(T, P):
    """El precio de D corregido con el de D-6, que cae en el mismo dia de la semana que D+1.

    ancla = D + w * (D-6 - D), con un w en [0, 1] por dia de la semana de D+1 y hora, ajustado
    por minimos cuadrados SOLO con train. Donde D ya se parece a D+1 (martes a viernes) w sale
    pequeno; el lunes, con D en domingo, pesa mas la semana anterior. Las dos columnas estan
    publicadas a las 11:00 de D.

    Devuelve (ancla (dias, 24) en EUR/MWh, pesos (7, 24) con lunes = 0).
    """
    X = filas(P, T.fechas, ["es_esios_D", "es_esios_Dm6"])
    D, D6 = (X[c].to_numpy().reshape(-1, 24) for c in ("es_esios_D", "es_esios_Dm6"))
    if not np.allclose(D, T.naive, atol=1e-3):
        raise ValueError("es_esios_D de la matriz plana no coincide con el naive del tensor: "
                         "las filas estan desalineadas")
    dow = pd.to_datetime(T.fechas).dayofweek.to_numpy()
    a, r = D6 - D, T.y - D
    pesos = np.zeros((7, 24))
    for g in range(7):
        m = T.tr & (dow == g)
        pesos[g] = np.clip((a[m] * r[m]).sum(0) / np.maximum((a[m] ** 2).sum(0), 1e-9), 0, 1)
    return D + pesos[dow] * a, pesos


def copia(y, p, D):
    """Cuanto se pega una prediccion al precio de D. None si no hay ningun dia con prediccion.

    movimiento_%  |pred - D| medio sobre |real - D| medio. 0 es copiar D; 100, moverse tanto
                  como la realidad. Quedarse por debajo de 100 no es malo en si: con
                  incertidumbre, quedarse corto minimiza el error -- multiplicar el movimiento
                  del ensemble de 06 por su factor de minimos cuadrados (1,14) subia su MAE de
                  12,13 a 12,27. Lo que delata la copia es este numero junto a `forma_vs_D`.
    corr_cambio   correlacion entre (pred - D) y (real - D): si acierta hacia donde cambia.
    forma_vs_D    correlacion media del perfil horario de cada dia, sin su nivel, contra el de
                  D. El real da ~0,86 en test; un modelo muy por encima esta copiando la curva.
    """
    ok = ~np.isnan(p).any(axis=1)
    if not ok.any():
        return None
    y, p, D = y[ok], p[ok], D[ok]
    dp, dr = (p - D).ravel(), (y - D).ravel()
    a, b = p - p.mean(1, keepdims=True), D - D.mean(1, keepdims=True)
    den = np.sqrt((a * a).sum(1) * (b * b).sum(1))
    forma = float(((a * b).sum(1)[den > 0] / den[den > 0]).mean()) if (den > 0).any() else np.nan
    return {"movimiento_%": float(100 * np.abs(dp).mean() / max(np.abs(dr).mean(), 1e-9)),
            "corr_cambio": (float(np.corrcoef(dp, dr)[0, 1])
                            if dp.std() > 0 and dr.std() > 0 else np.nan),
            "forma_vs_D": forma}


# ── Medir, guardar, leer ──────────────────────────────────────────────────────────────────
def medir(y, p, prueba=False):
    """`EF.metricas` sobre los dias con prediccion. Fuera de --prueba no puede faltar ninguno."""
    ok = ~np.isnan(p).any(axis=1)
    if not ok.all() and not prueba:
        raise ValueError(f"{int((~ok).sum())} dias sin prediccion")
    if not ok.any():
        return None
    return {**EF.metricas(y[ok], p[ok]), "n_dias": int(ok.sum())}


def guardar(fam, s, modelo, extra, T, salida, destip):
    """Artefacto + `.preprocesado.json`. Sin lo segundo, un modelo cargado en produccion recibe
    columnas en otro orden o sin tipificar y devuelve numeros plausibles y equivocados."""
    base = salida / f"{fam}__s{s}"
    if fam in REDES:
        modelo.save(base.with_suffix(".keras"))     # BosqueHorario se guarda en carpeta propia
        pre = T.preprocesado()
        pre["familia"] = fam
        pre["objetivo"] = "absoluto" if fam == "seq2seq_absoluto" else "residuo"
        pre["destipificar"] = destip["absoluto" if fam == "seq2seq_absoluto" else "residuo"]
        pre["entrada"] = "plana" if fam in ("denso", "boosting") else "tensores"
    else:
        pre = {"hash_matriz": T.meta.get("hash"), "familia": fam, "autor": AUTOR[fam],
               "objetivo": "absoluto", "features": extra.get("features", []),
               "entrada": ("serie horaria (fecha_objetivo + hora)" if fam.startswith("sarima")
                           else "matriz plana (fecha_objetivo, hora)")}
        if fam == "lgbm_nucleo":
            modelo.booster_.save_model(str(base.with_suffix(".txt")))
            pre["hiperparametros"] = extra["hiperparametros"]
        elif fam in ("ridge", "elasticnet"):
            import joblib
            joblib.dump(modelo, base.with_suffix(".joblib"))
            pre["hiperparametros"] = extra["hiperparametros"]
            pre["nota"] = "Pipeline con el StandardScaler dentro: acepta la matriz cruda"
        else:
            pre["orden"] = extra["orden"]
            if "escalado_exog" in extra:
                pre["escalado_exog"] = extra["escalado_exog"]
            pre["parametros"] = {str(k): float(v) for k, v in modelo.params.items()}
            pre["nota"] = ("Sin binario: un SARIMAXResults no predice desde una fila, hay que "
                           "extenderle el estado dia a dia (ver scripts/modelos_samuel.py)")
    (salida / f"{fam}.preprocesado.json").write_text(
        json.dumps(pre, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def cargar_resultados(matriz="nucleo", prueba=False, ancla=ANCLA_DEFECTO, vetos=VETOS_DEFECTO):
    """Lo que deja `ejecutar`, o None si aun no se ha entrenado."""
    s = carpeta_salida(matriz, prueba, ancla, vetos)
    if not (s / "resumen.csv").exists():
        return None
    return {"salida": s, "por_semilla": pd.read_csv(s / "por_semilla.csv"),
            "resumen": pd.read_csv(s / "resumen.csv", index_col=0),
            "meta": json.loads((s / "meta.json").read_text(encoding="utf-8"))}


def leer_extras(salida, fam):
    f = Path(salida) / f"extras_{fam}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def importancias_lgbm(salida):
    """Ganancia media por feature de los `lgbm_nucleo__s*.txt` guardados, o None."""
    txt = sorted(Path(salida).glob("lgbm_nucleo__s*.txt"))
    if not txt:
        return None
    import lightgbm as lgb
    boosters = [lgb.Booster(model_file=str(t)) for t in txt]
    imp = pd.concat([pd.Series(b.feature_importance("gain"), index=b.feature_name())
                     for b in boosters], axis=1).mean(axis=1)
    return imp.sort_values(ascending=False).rename("ganancia_media").to_frame()


def _ensemble(d, candidatas, salida, T, nv, prueba):
    """Mejor representante de cada familia SEGUN VALIDACION, solo si bate al naive en validacion."""
    c = d[d.familia.isin(candidatas) & d.MAE_val.notna()]
    if c.empty:
        return None
    mejor = c.loc[c.groupby("familia")["MAE_val"].idxmin()]
    miembros = [f"{r.familia}__s{int(r.semilla) - SEMILLA}" for r in mejor.itertuples()
                if r.MAE_val < nv["MAE"]]
    if len(miembros) < 2:
        return None

    def leer(tramo, k):
        return pd.read_csv(salida / f"pred_{tramo}_{k}.csv", index_col=0).to_numpy()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)     # dias sin prediccion en --prueba
        e_va = np.nanmean([leer("val", k) for k in miembros], axis=0)
        e_te = np.nanmean([leer("test", k) for k in miembros], axis=0)
    return miembros, e_va, e_te, medir(T.y[T.va], e_va, prueba), medir(T.y[T.te], e_te, prueba)


def _ensemble_seleccion(d, candidatas, salida, T, prueba, pasos=30):
    """Seleccion voraz de ensembles (Caruana et al., 2004), decidida SOLO con validacion.

    La regla de `_ensemble` deja entrar a cualquier familia que bata al naive en validacion, y
    con eso entraron ridge y sarimax, peores que la persistencia en test: un tribunal pregunta
    con razon que hace en el sistema final un modelo que empeora a no hacer nada. Aqui, en cada
    paso se anade -con reemplazo- el representante de familia que mas baja el MAE de validacion
    de la media, y se queda el tamano con menor error. Un modelo solo entra si mejora al
    conjunto, y las veces que se elige son su peso. El MAE de validacion resultante es
    optimista (se ha optimizado sobre el); el de test es la medida honesta.
    """
    from collections import Counter
    c = d[d.familia.isin(candidatas) & d.MAE_val.notna()]
    if c.empty:
        return None
    mejor = c.loc[c.groupby("familia")["MAE_val"].idxmin()]
    claves = [f"{r.familia}__s{int(r.semilla) - SEMILLA}" for r in mejor.itertuples()]

    def leer(tramo, k):
        return pd.read_csv(salida / f"pred_{tramo}_{k}.csv", index_col=0).to_numpy()

    pv, pt = {k: leer("val", k) for k in claves}, {k: leer("test", k) for k in claves}
    yv = T.y[T.va]
    suma, elegidos, curva = np.zeros(yv.shape), [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)     # dias sin prediccion en --prueba
        for paso in range(1, pasos + 1):
            error = {k: float(np.nanmean(np.abs((suma + pv[k]) / paso - yv))) for k in claves}
            k = min(error, key=error.get)
            suma += pv[k]
            elegidos.append(k)
            curva.append(error[k])
    n = int(np.argmin(curva)) + 1
    pesos = {k: v / n for k, v in Counter(elegidos[:n]).most_common()}
    e_va = sum(pv[k] * w for k, w in pesos.items())
    e_te = sum(pt[k] * w for k, w in pesos.items())
    pesos = {k: round(w, 4) for k, w in pesos.items()}
    return pesos, e_va, e_te, medir(T.y[T.va], e_va, prueba), medir(T.y[T.te], e_te, prueba)


# ── Orquestacion ──────────────────────────────────────────────────────────────────────────
def ejecutar(matriz="nucleo", semillas=3, familias=None, guardar_modelos=False,
             rebuscar_orden=False, prueba=False, ancla=ANCLA_DEFECTO, vetos=VETOS_DEFECTO):
    familias = list(familias or FAMILIAS)
    raras = [f for f in familias if f not in FAMILIAS]
    if raras:
        raise ValueError(f"familias desconocidas {raras}; disponibles: {FAMILIAS}")
    if ancla not in ANCLAS:
        raise ValueError(f"ancla desconocida {ancla!r}; disponibles: {ANCLAS}")
    vetos = tuple(sorted(set(vetos)))
    raros = [v for v in vetos if v not in VETOS]
    if raros:
        raise ValueError(f"vetos desconocidos {raros}; disponibles: {list(VETOS)}")
    from preparar_tensores import preparar, residuo

    salida = carpeta_salida(matriz, prueba, ancla, vetos)
    salida.mkdir(parents=True, exist_ok=True)
    trayport = comprobar_trayport(matriz)
    print(f"Trayport vetado {list(TRAYPORT)} · presente en la matriz: "
          f"{trayport['presentes'] or 'ninguna'}")
    fuera = columnas_vetadas(matriz, vetos)
    _VETADAS.clear()
    _VETADAS.update(fuera)
    for v in vetos:
        cv = [c for c in fuera if VETOS[v](c)]
        print(f"Veto de frontera {v}: {len(cv)} columnas {cv[:3]}{' ...' if len(cv) > 3 else ''}")
    print(f"Ancla del residuo: {ancla}")

    keras = layers = None
    if any(f in REDES for f in familias):
        import tensorflow as tf
        from tensorflow import keras
        from tensorflow.keras import layers
        print("TensorFlow", tf.__version__, "| GPU:",
              bool(tf.config.list_physical_devices("GPU")))
    if prueba:
        EF.EPOCHS = 2

    T = preparar(matriz, excluir=(*TRAYPORT, *fuera))
    vetar([*T.canales, *T.cols_dec, *T.cols_est], "tensores")
    P = cargar_plana(matriz, fuera)            # siempre: el ancla semanal lee D-6 de aqui
    mapa = {"decoder": frontera(T.cols_dec), "plana": frontera(numericas(P, NO_FEATURES_SAMUEL))}
    print("   matriz plana por dia que describe: "
          + " · ".join(f"{g} {len(c)}" for g, c in mapa["plana"].items()))
    if ancla == "semanal":
        base, pesos = ancla_semanal(T, P)
        TA = dataclasses.replace(T, naive=base)   # `residuo` lee TA.naive tambien al destipificar
    else:
        base, pesos, TA = T.naive, None, T
    yr, inv_r, mu_r, sd_r = residuo(TA)
    mu_y, sd_y = float(T.y[T.tr].mean()), float(T.y[T.tr].std())
    ys = ((T.y - mu_y) / sd_y).astype("float32")

    def inv_abs(p, m):
        return p * sd_y + mu_y

    destip = {"absoluto": {"mu": mu_y, "sd": sd_y, "nota": "y = pred*sd + mu"},
              "residuo": {"mu": mu_r, "sd": sd_r, "ancla": ancla,
                          "nota": ("y = pred*sd + mu + naive(dia D)" if pesos is None else
                                   "y = pred*sd + mu + ancla; ancla = D + pesos_Dm6[dia de la "
                                   "semana de D+1, lunes=0][hora] * (D-6 - D), con D = "
                                   "es_esios_D y D-6 = es_esios_Dm6"),
                          **({"pesos_Dm6": pesos.round(4).tolist()} if pesos is not None else {})}}

    nv = EF.metricas(T.y[T.va], T.naive[T.va])
    nt = EF.metricas(T.y[T.te], T.naive[T.te])
    print(f"   naive: val {nv['MAE']:.2f} · test {nt['MAE']:.2f} · captura val {nv['captura_%']:.1f}%")
    av, at = EF.metricas(T.y[T.va], base[T.va]), EF.metricas(T.y[T.te], base[T.te])
    if pesos is not None:
        print(f"   ancla semanal: val {av['MAE']:.2f} · test {at['MAE']:.2f} · "
              f"peso medio de D-6 {pesos.mean():.2f} (lunes {pesos[0].mean():.2f})")
    real = copia(T.y[T.te], T.y[T.te], T.naive[T.te])
    print(f"   la realidad: perfil de D+1 contra el de D {real['forma_vs_D']:.3f} en test "
          "-- un modelo muy por encima esta copiando D")

    cols = [f"h{h:02d}" for h in range(24)]
    fechas_va, fechas_te = pd.to_datetime(T.fechas[T.va]), pd.to_datetime(T.fechas[T.te])

    # Resume, como en entrenar_finales: lanzar por partes con --familias no borra lo anterior.
    csv = salida / "por_semilla.csv"
    registro = pd.read_csv(csv).to_dict("records") if csv.exists() else []
    hechos = {(r["familia"], int(r["semilla"])) for r in registro}
    total = sum(1 if f in DETERMINISTAS else semillas for f in familias)
    print(f"   {len(familias)} familias · {total} entrenamientos"
          + (f" · {len(hechos)} ya hechos" if hechos else ""))
    print()

    t0 = time.time()
    for fam in familias:
        for s in range(1 if fam in DETERMINISTAS else semillas):
            if (fam, SEMILLA + s) in hechos:
                continue
            if fam in REDES:
                pv, pt, npar, modelo = EF.entrenar(fam, T, yr, inv_r, ys, inv_abs,
                                                   SEMILLA + s, keras, layers)
                extra = {}
            elif fam == "lgbm_nucleo":
                pv, pt, npar, modelo, extra = entrenar_lgbm_nucleo(T, P, SEMILLA + s, prueba)
            elif fam in ("ridge", "elasticnet"):
                pv, pt, npar, modelo, extra = entrenar_lineal(fam, T, P, prueba)
            else:
                pv, pt, npar, modelo, extra = entrenar_sarima(fam, T, P, rebuscar_orden, prueba)

            mv, mt = medir(T.y[T.va], pv, prueba), medir(T.y[T.te], pt, prueba)
            cp = copia(T.y[T.te], pt, T.naive[T.te])
            registro.append({
                "familia": fam, "autor": AUTOR[fam], "tipo": TIPO[fam], "semilla": SEMILLA + s,
                "parametros": npar,
                "MAE_val": round(mv["MAE"], 3) if mv else np.nan,
                "MAE_test": round(mt["MAE"], 3) if mt else np.nan,
                "captura_val_%": round(mv["captura_%"], 2) if mv else np.nan,
                "captura_test_%": round(mt["captura_%"], 2) if mt else np.nan,
                "pico_1h_test_%": round(mt["pico_1h_%"], 2) if mt else np.nan,
                "vs_naive_val_%": round(100 * (mv["MAE"] / nv["MAE"] - 1), 1) if mv else np.nan,
                "movimiento_test_%": round(cp["movimiento_%"], 1) if cp else np.nan,
                "corr_cambio_test": round(cp["corr_cambio"], 3) if cp else np.nan,
                "forma_vs_D_test": round(cp["forma_vs_D"], 3) if cp else np.nan,
                "n_dias_val": mv["n_dias"] if mv else 0,
                "n_dias_test": mt["n_dias"] if mt else 0})
            pd.DataFrame(registro).to_csv(csv, index=False)
            pd.DataFrame(pv, index=fechas_va, columns=cols).to_csv(salida / f"pred_val_{fam}__s{s}.csv")
            pd.DataFrame(pt, index=fechas_te, columns=cols).to_csv(salida / f"pred_test_{fam}__s{s}.csv")
            if extra:
                if "rejilla" in extra:
                    extra.pop("rejilla").to_csv(salida / f"tuning_{fam}.csv", index=False)
                (salida / f"extras_{fam}.json").write_text(
                    json.dumps(extra, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            if guardar_modelos and modelo is not None:
                guardar(fam, s, modelo, extra, T, salida, destip)

            def f3(m, k="MAE"):
                return f"{m[k]:6.3f}" if m else "     -"
            print(f"   {fam:18s} s{s}  MAE val {f3(mv)} · test {f3(mt)} · "
                  f"captura {f3(mt, 'captura_%')}% · perfil vs D {f3(cp, 'forma_vs_D')}   "
                  f"[{(time.time() - t0) / 60:.0f} min]")

    d = pd.DataFrame(registro)
    for c in COLS_COPIA:                      # un por_semilla.csv anterior no las tiene
        if c not in d.columns:
            d[c] = np.nan
    res = (d.groupby("familia").agg(
        autor=("autor", "first"), tipo=("tipo", "first"), n=("semilla", "size"),
        parametros=("parametros", "first"),
        MAE_val=("MAE_val", "mean"), sd_val=("MAE_val", "std"),
        MAE_test=("MAE_test", "mean"), sd_test=("MAE_test", "std"),
        captura_test=("captura_test_%", "mean"), pico_1h=("pico_1h_test_%", "mean"),
        movimiento=("movimiento_test_%", "mean"), forma_vs_D=("forma_vs_D_test", "mean"))
        .round(3))
    res["vs_naive_test_%"] = (100 * (res.MAE_test / nt["MAE"] - 1)).round(1)

    def fila(autor, tipo, n, parametros, ev, et, ce):
        return {"autor": autor, "tipo": tipo, "n": n, "parametros": parametros,
                "MAE_val": round(ev["MAE"], 3) if ev else np.nan, "sd_val": np.nan,
                "MAE_test": round(et["MAE"], 3) if et else np.nan, "sd_test": np.nan,
                "captura_test": round(et["captura_%"], 3) if et else np.nan,
                "pico_1h": round(et["pico_1h_%"], 3) if et else np.nan,
                "movimiento": round(ce["movimiento_%"], 1) if ce else np.nan,
                "forma_vs_D": round(ce["forma_vs_D"], 3) if ce else np.nan,
                "vs_naive_test_%": round(100 * (et["MAE"] / nt["MAE"] - 1), 1) if et else np.nan}

    # Dos ensembles: el de 06 (las 8 familias, para seguir la serie) y el de todas.
    ensembles = {}
    for nombre, candidatas in (("ensemble", REDES), ("ensemble_todos", FAMILIAS)):
        e = _ensemble(d, candidatas, salida, T, nv, prueba)
        if e is None:
            continue
        miembros, e_va, e_te, ev, et = e
        ensembles[nombre] = miembros
        pd.DataFrame(e_va, index=fechas_va, columns=cols).to_csv(salida / f"pred_val_{nombre}.csv")
        pd.DataFrame(e_te, index=fechas_te, columns=cols).to_csv(salida / f"pred_test_{nombre}.csv")
        res.loc[nombre] = fila("equipo", "ensemble", len(miembros), np.nan, ev, et,
                               copia(T.y[T.te], e_te, T.naive[T.te]))
    sel = _ensemble_seleccion(d, FAMILIAS, salida, T, prueba)
    if sel is not None:
        pesos_sel, e_va, e_te, ev, et = sel
        ensembles["ensemble_seleccion"] = pesos_sel
        pd.DataFrame(e_va, index=fechas_va, columns=cols).to_csv(salida / "pred_val_ensemble_seleccion.csv")
        pd.DataFrame(e_te, index=fechas_te, columns=cols).to_csv(salida / "pred_test_ensemble_seleccion.csv")
        res.loc["ensemble_seleccion"] = fila("equipo", "ensemble", len(pesos_sel), np.nan, ev, et,
                                             copia(T.y[T.te], e_te, T.naive[T.te]))
    res.loc["naive (persistencia)"] = fila("-", "referencia", 0, 0, nv, nt,
                                           copia(T.y[T.te], T.naive[T.te], T.naive[T.te]))
    if pesos is not None:
        res.loc["ancla semanal"] = fila("-", "referencia", 0, 0, av, at,
                                        copia(T.y[T.te], base[T.te], T.naive[T.te]))
    # Referencias para el notebook, con prefijo `ref_` para que no las recoja un glob de `pred_`.
    for tramo, m, fe in (("val", T.va, fechas_va), ("test", T.te, fechas_te)):
        pd.DataFrame(T.y[m], index=fe, columns=cols).to_csv(salida / f"ref_{tramo}_real.csv")
        pd.DataFrame(T.naive[m], index=fe, columns=cols).to_csv(salida / f"ref_{tramo}_naive.csv")
        pd.DataFrame(base[m], index=fe, columns=cols).to_csv(salida / f"ref_{tramo}_ancla.csv")
    res = res.sort_values("MAE_val")
    res.to_csv(salida / "resumen.csv")

    ordenes = {f: (leer_extras(salida, f) or {}).get("orden") for f in ("sarima", "sarimax")}
    (salida / "meta.json").write_text(json.dumps({
        "matriz": matriz, "hash": T.meta.get("hash"), "semillas": semillas, "prueba": prueba,
        "naive_val_MAE": round(nv["MAE"], 3), "naive_test_MAE": round(nt["MAE"], 3),
        "dias_train": int(T.tr.sum()), "dias_val": int(T.va.sum()), "dias_test": int(T.te.sum()),
        "canales_encoder": int(T.X_enc.shape[-1]), "columnas_decoder": int(T.X_dec.shape[-1]),
        "estaticos": int(T.X_est.shape[-1]),
        "trayport": {"vetadas": list(TRAYPORT), "presentes_en_matriz": trayport["presentes"]},
        "vetos_frontera": {v: [c for c in fuera if VETOS[v](c)] for v in vetos},
        "frontera": {k: {g: len(c) for g, c in m.items()} for k, m in mapa.items()},
        "frontera_ree_prev": mapa["plana"].get("D+1 prevision REE", []),
        "ancla": {"tipo": ancla, "MAE_val": round(av["MAE"], 3), "MAE_test": round(at["MAE"], 3),
                  "pesos_Dm6": pesos.round(4).tolist() if pesos is not None else None},
        "forma_real_vs_D_test": round(real["forma_vs_D"], 3),
        "familias": sorted(d.familia.unique()), "ensembles": ensembles,
        "lgbm_willy": LGBM_WILLY, "ordenes_sarima": ordenes,
        "generado": time.strftime("%Y-%m-%d %H:%M"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 96)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(res.to_string())
    print()
    for nombre, miembros in ensembles.items():
        print(f"{nombre}: {miembros}")
    individuales = res[~res.tipo.isin(["ensemble", "referencia"])].dropna(subset=["MAE_test"])
    if len(individuales):
        print(f"   mejor MAE     : {individuales.MAE_test.idxmin()}")
        print(f"   mejor captura : {individuales.captura_test.idxmax()}")
    print(f"\nGuardado en {salida}")
    return salida


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matriz", default="nucleo")
    ap.add_argument("--semillas", type=int, default=3)
    ap.add_argument("--familias", nargs="+", default=FAMILIAS, choices=FAMILIAS)
    ap.add_argument("--guardar-modelos", action="store_true",
                    help="escribe el artefacto de cada semilla y su .preprocesado.json")
    ap.add_argument("--rebuscar-orden", action="store_true",
                    help="repite el auto_arima de Samuel en vez de reutilizar sus ordenes (horas)")
    ap.add_argument("--prueba", action="store_true",
                    help="humo: 1 semilla, 2 epocas, 60 arboles, SARIMA sobre 5 dias; carpeta *_prueba")
    ap.add_argument("--ancla", choices=ANCLAS, default=ANCLA_DEFECTO,
                    help="contra que se toma el residuo: 'semanal' (D corregido con D-6) o 'D' (06)")
    ap.add_argument("--vetar", nargs="*", choices=list(VETOS), default=list(VETOS_DEFECTO),
                    help="grupos fuera por frontera de produccion; sin argumentos, ninguno "
                         "(Trayport sale siempre)")
    a = ap.parse_args()
    ejecutar(a.matriz, 1 if a.prueba else a.semillas, a.familias, a.guardar_modelos,
             a.rebuscar_orden, a.prueba, a.ancla, a.vetar)


if __name__ == "__main__":
    main()
