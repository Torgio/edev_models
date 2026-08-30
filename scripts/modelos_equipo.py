"""Los arboles de Magdalena y Willy, servidos con el mismo contrato que los nuestros.

QUE HAY Y QUE NO
De los seis modelos que entregaron los companeros, tres se pueden cargar y tres no:

    lightgbm  (Magdalena, 3 semillas, .txt)    matriz nucleo, 127 features   SI
    xgboost   (Magdalena, 3 semillas, .json)   matriz nucleo, 127 features   SI
    lgbm_nucleo (Willy, 1 semilla, .txt)       matriz nucleo, 122 features   SI

    lgbm_horario_afinado_cqr (Willy)   el .joblib no esta en el repo, y 152 de sus 156
                                       columnas no son de la matriz nucleo
    ridge, elasticnet (Samuel)         falta el modelo.joblib que pide su propio DEPLOY.md
    sarima, sarimax   (Samuel)         idem; ademas van sobre otro dataset

De los tres que faltan solo tenemos `pred_val_2025.csv`. Sirve para la tabla comparativa de
la memoria; no sirve para predecir un dia nuevo.

EN QUE SE DIFERENCIAN DE LOS NUESTROS
Nuestras redes comen el tensor (encoder de 168 h, decoder de 24 y estaticos). Estos comen
UNA FILA DE LA MATRIZ POR HORA, plana, y predicen el precio en EUR/MWh directamente -- no el
residuo contra el naive. Asi que no pasan por `Tensores.ent()` ni por el destipificado: leen
la matriz, ordenan las columnas como pide su metadata y predicen.

Lo unico que comparten con `predecir.Miembro` es la firma: `predecir(T, m)` devuelve un
array (dias, 24) en EUR/MWh, alineado con `T.fechas[m]`. Con eso entran en `Predictor` y en
el ensemble sin tocar nada mas.

    python scripts/modelos_equipo.py --listar
    python scripts/modelos_equipo.py --evaluar
    python scripts/modelos_equipo.py --modelo lightgbm --desde 2026-08-30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))

# `nombre -> (metadata, clave de la lista de features, plantilla del artefacto, semillas)`.
# Las semillas van como sufijo del fichero: `lightgbm__s0.txt`, `s1`, `s2`.
INVENTARIO = {
    "lightgbm": {
        "autor": "Magdalena",
        "meta": REPO / "modelos" / "ML_Magui" / "metadata_lightgbm.json",
        "clave_features": "features",
        "artefacto": REPO / "modelos" / "ML_Magui" / "artefactos" / "lightgbm__s{s}.txt",
        "semillas": [0, 1, 2],
        "motor": "lgb"},
    "xgboost": {
        "autor": "Magdalena",
        "meta": REPO / "modelos" / "ML_Magui" / "metadata_xgboost.json",
        "clave_features": "features",
        "artefacto": REPO / "modelos" / "ML_Magui" / "artefactos" / "xgboost__s{s}.json",
        "semillas": [0, 1, 2],
        "motor": "xgb"},
    "lgbm_nucleo": {
        "autor": "Willy",
        "meta": REPO / "modelos" / "lightgbm_nucleo_export" / "metadata.json",
        "clave_features": "feature_cols_en_orden",
        "artefacto": REPO / "modelos" / "lightgbm_nucleo_export" / "modelo.txt",
        "semillas": [0],
        "motor": "lgb"},
}

_CACHE: dict[str, pd.DataFrame] = {}


def matriz_plana(matriz="produccion") -> pd.DataFrame:
    """La matriz indexada por (fecha_objetivo, hora), que es como la piden estos modelos.

    Se cachea: los tres cargadores la comparten y son 58.000 filas x 133 columnas.
    """
    if matriz in _CACHE:
        return _CACHE[matriz]
    ruta = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    try:
        df = pd.read_parquet(ruta)
    except Exception:
        # Mismo motivo que en `preparar_tensores`: el pyarrow de Windows no abre los
        # parquet escritos desde WSL. El CSV tiene lo mismo.
        df = pd.read_csv(ruta.with_suffix(".csv"),
                         parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
    df = df.set_index([pd.to_datetime(df.fecha_objetivo), df.hora.astype(int)])
    df.index.names = ["fecha_objetivo", "hora"]
    _CACHE[matriz] = df
    return df


class ArbolPlano:
    """Un arbol de los companeros. Duck-type de `predecir.Miembro`."""

    def __init__(self, familia, semilla, matriz="produccion"):
        cfg = INVENTARIO[familia]
        self.familia, self.semilla, self.matriz = familia, semilla, matriz
        self.autor, self.motor = cfg["autor"], cfg["motor"]
        meta = json.loads(Path(cfg["meta"]).read_text(encoding="utf-8"))
        self.features = list(meta[cfg["clave_features"]])
        self.hash_entrenamiento = meta.get("hash_matriz")
        # Todos predicen `target_price`, no el residuo. Lo dice Willy en su metadata y en
        # el `guardar_artefactos.py` de Magdalena (`TARGET = "target_price"`).
        self.absoluto = True
        ruta = Path(str(cfg["artefacto"]).replace("{s}", str(semilla)))
        if not ruta.exists():
            raise FileNotFoundError(f"{self}: no esta {ruta}")
        self._avisado = False
        if self.motor == "lgb":
            import lightgbm as lgb
            self.modelo = lgb.Booster(model_file=str(ruta))
        else:
            try:
                import xgboost as xgb
            except ModuleNotFoundError:
                raise ModuleNotFoundError(
                    "xgboost no esta instalado y los artefactos de Magdalena lo necesitan:"
                    "\n    pip install 'xgboost>=3.4'") from None
            self.modelo = xgb.XGBRegressor()
            self.modelo.load_model(str(ruta))

    def __repr__(self):
        return f"{self.familia}__s{self.semilla}"

    def _avisa(self, T):
        if self._avisado:
            return
        self._avisado = True
        h = T.meta.get("hash")
        if self.hash_entrenamiento and h and h != self.hash_entrenamiento:
            print(f"  aviso · {self}: la matriz no es la del entrenamiento "
                  f"({self.hash_entrenamiento} -> {h}); si solo se le han anadido dias, "
                  f"es lo esperado", file=sys.stderr)

    def filas(self, fechas) -> pd.DataFrame:
        """Las 24 filas de cada dia objetivo, en orden dia-mayor y con SUS columnas.

        Si falta alguna fila el reindex la deja a NaN y aqui se para: un arbol con NaN no
        falla, devuelve un numero, y seria un numero inventado.
        """
        df = matriz_plana(self.matriz)
        idx = pd.MultiIndex.from_product([pd.to_datetime(fechas), range(24)],
                                         names=["fecha_objetivo", "hora"])
        X = df.reindex(idx)[self.features]
        if X.isna().any().any():
            faltan = X.index[X.isna().any(axis=1)]
            raise ValueError(
                f"{self}: {len(faltan)} filas incompletas en la matriz, la primera "
                f"{faltan[0]}. Reconstruye la matriz antes de predecir.")
        return X

    def predecir(self, T, m):
        self._avisa(T)
        X = self.filas(T.fechas[m])
        p = np.asarray(self.modelo.predict(X), dtype="float64")
        return p.reshape(-1, 24)


def cargar(modelo="todos", matriz="produccion", semilla=None):
    """Devuelve un `predecir.Predictor` con los arboles del equipo que se pidan."""
    from predecir import Predictor
    nombres = list(INVENTARIO) if modelo in ("todos", "equipo") else [modelo]
    if any(n not in INVENTARIO for n in nombres):
        raise KeyError(f"modelos disponibles: {', '.join(INVENTARIO)}, todos")
    miembros = []
    for n in nombres:
        ss = [semilla] if semilla is not None else INVENTARIO[n]["semillas"]
        miembros += [ArbolPlano(n, s, matriz) for s in ss]
    return Predictor(f"equipo:{modelo}", miembros, matriz)


def evaluar(matriz="produccion", tramo="va"):
    """MAE de cada arbol y de cada semilla, contra el precio real de ese tramo.

    Sobre validacion por defecto: es donde se eligen representantes sin mirar test, que es
    la regla que dice seguir la metadata de Magdalena.
    """
    from preparar_tensores import preparar
    T = preparar(matriz, verbose=False)
    m = {"tr": T.tr, "va": T.va, "te": T.te}[tramo]
    # `T.y` sale de `PRECIO[t_idx]` y no pasa por ningun escalador: ya son EUR/MWh.
    real = T.y[m]

    filas = []
    for n in INVENTARIO:
        for s in INVENTARIO[n]["semillas"]:
            try:
                a = ArbolPlano(n, s, matriz)
            except (FileNotFoundError, ModuleNotFoundError) as e:
                filas.append({"modelo": f"{n}__s{s}", "autor": INVENTARIO[n]["autor"],
                              "MAE": np.nan, "nota": str(e).split("\n")[0][:60]})
                continue
            p = a.predecir(T, m)
            filas.append({"modelo": str(a), "autor": a.autor,
                          "MAE": float(np.abs(p - real).mean()), "nota": ""})
    naive = float(np.abs(T.naive[m] - real).mean())
    return pd.DataFrame(filas), naive, int(m.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--evaluar", action="store_true")
    ap.add_argument("--modelo", default="todos")
    ap.add_argument("--matriz", default="produccion")
    ap.add_argument("--tramo", default="va", choices=["tr", "va", "te", "todo"])
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    ap.add_argument("--semilla", type=int)
    a = ap.parse_args()

    if a.listar:
        print(f"\n  {'modelo':14s} {'autor':11s} {'sem':>4s} {'feats':>6s}  artefacto")
        print("  " + "-" * 78)
        for n, c in INVENTARIO.items():
            f = json.loads(Path(c["meta"]).read_text(encoding="utf-8"))[c["clave_features"]]
            for s in c["semillas"]:
                r = Path(str(c["artefacto"]).replace("{s}", str(s)))
                print(f"  {n:14s} {c['autor']:11s} s{s:<3d} {len(f):6d}  "
                      f"{r.relative_to(REPO)}{'' if r.exists() else '   NO ESTA'}")
        return

    if a.evaluar:
        d, naive, n = evaluar(a.matriz, a.tramo if a.tramo != "todo" else "va")
        print(f"\n  MAE sobre {a.tramo} · {n} dias · matriz {a.matriz}")
        print(f"  {'modelo':20s} {'autor':11s} {'MAE':>8s}   nota")
        print("  " + "-" * 62)
        for r in d.sort_values("MAE").itertuples():
            print(f"  {r.modelo:20s} {r.autor:11s} "
                  f"{('%8.3f' % r.MAE) if r.MAE == r.MAE else '       -'}   {r.nota}")
        print("  " + "-" * 62)
        print(f"  {'naive (precio de D)':32s} {naive:8.3f}")
        return

    p = cargar(a.modelo, a.matriz, a.semilla)
    print(p)
    y = p.predecir(desde=a.desde, hasta=a.hasta, tramo=a.tramo)
    print(f"\n{len(y)} dias x 24 h · media {y.values.mean():.2f} EUR/MWh · "
          f"min {y.values.min():.2f} · max {y.values.max():.2f}")
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(y.round(2).to_string())


if __name__ == "__main__":
    main()
