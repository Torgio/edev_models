"""Ridge y ElasticNet servidos por la puerta que usa de verdad `run_diario.py`.

POR QUE ESTE FICHERO Y NO UN predict.py
`artifact_prod.docx` (cap. 5) describe un contrato que en este repo NO existe:
`predecir(fecha_objetivo, ctx) -> 24 filas`, con `ctx.features`, `ctx.engine` y un
orquestador que importa un modulo por `modelo_id`. Lo que hay instalado es otra
cosa: `run_diario.py` no importa predict.py de nadie. Llama a
`guardar_predicciones.produccion(..., equipo=True)`, y los modelos entran por
`modelos_equipo.INVENTARIO` con la firma de `predecir.Miembro`:

    predecir(T, m) -> np.ndarray (dias, 24) en EUR/MWh, alineado con T.fechas[m]

Asi que el envoltorio correcto es este, no un predict.py. Es literalmente la clase
`ArbolPlano` de Torgio con otro cargador: leer la matriz plana, reindexar por
(fecha_objetivo, hora), coger SUS columnas en SU orden y predecir en EUR/MWh
absolutos. Se reutiliza `ArbolPlano` entera y solo se cambia como se carga el
artefacto, para no duplicar el manejo de los cambios de hora -- el domingo de marzo
le falta la hora 2 y el de octubre la repite, y eso ya esta resuelto ahi.

QUE ENTRA Y QUE NO
    ridge_horario       joblib, Pipeline sklearn        SI
    elasticnet_horario  joblib, Pipeline sklearn        SI
    sarima_horario      SARIMAXResults                  NO -- ver abajo
    sarimax_horario     SARIMAXResults                  NO -- ver abajo

Los dos lineales encajan porque predicen fila a fila: una hora de matriz entra, un
precio sale, sin estado. Y desde el arreglo del escalador su `modelo.joblib` acepta
la matriz CRUDA, que es exactamente lo que da `matriz_plana`.

SARIMA y SARIMAX no encajan y no es cuestion de escribir mas codigo. Un
`SARIMAXResults` no predice desde una fila: continua una serie. Para darle el D+1 de
hoy hay que extender su estado con el precio real desde el fin de train, dia a dia,
que es lo que hacia `bloques24` en validacion. `predecir(T, m)` recibe una mascara de
dias sueltos y no garantiza ni orden ni continuidad, asi que meterlos aqui daria
numeros silenciosamente peores que los medidos. Necesitan su propio recorrido
secuencial; hasta entonces se quedan como entregable de leaderboard y memoria.

    python scripts/modelos_samuel.py --listar
    python scripts/modelos_samuel.py --evaluar
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

from modelos_equipo import ArbolPlano, INVENTARIO, matriz_plana  # noqa: E402,F401

ENTREGABLES = REPO / "modelos" / "ML_samuel" / "entregables"

# Los mismos campos que las entradas de Magdalena y Willy, para poder inyectarlas
# en INVENTARIO sin tocar el codigo de Torgio.
#
# `semillas: [0]` porque Ridge y ElasticNet son deterministas: no hay varianza por
# semilla que promediar, un solo ajuste ES el modelo. `clave_features` es "features",
# que es como lo escribe `entrega.guardar_entregable`.
MIOS = {
    "ridge_horario": {
        "autor": "Samuel",
        "meta": ENTREGABLES / "ridge_horario" / "metadata.json",
        "clave_features": "features",
        "artefacto": ENTREGABLES / "ridge_horario" / "modelo.joblib",
        "semillas": [0],
        "motor": "joblib"},
    "elasticnet_horario": {
        "autor": "Samuel",
        "meta": ENTREGABLES / "elasticnet_horario" / "metadata.json",
        "clave_features": "features",
        "artefacto": ENTREGABLES / "elasticnet_horario" / "modelo.joblib",
        "semillas": [0],
        "motor": "joblib"},
}


class LinealPlano(ArbolPlano):
    """`ArbolPlano` con cargador joblib. Todo lo demas se hereda tal cual."""

    def __init__(self, familia, semilla=0, matriz="produccion"):
        cfg = {**INVENTARIO, **MIOS}[familia]
        if cfg.get("motor") != "joblib":
            raise ValueError(f"{familia} no es un modelo joblib; usa ArbolPlano")

        import joblib

        self.familia, self.semilla, self.matriz = familia, semilla, matriz
        self.semilla_real = 42          # config.SEMILLA; los lineales no la usan
        self.autor, self.motor = cfg["autor"], "joblib"

        meta = json.loads(Path(cfg["meta"]).read_text(encoding="utf-8"))
        self.features = list(meta[cfg["clave_features"]])
        self.hash_entrenamiento = meta.get("hash_matriz")
        self.version = meta.get("version", "v1")
        self.absoluto = True            # predicen target_price, no el residuo
        self._avisado = False

        # El artefacto tiene que ser servible sobre features CRUDAS. Si es un
        # estimador pelado entrenado sobre features tipificadas, `predict` no falla:
        # devuelve numeros del orden de magnitud equivocado. Se comprueba al cargar
        # en vez de descubrirlo en la tabla `predictions`.
        if meta.get("interfaz") != "sklearn":
            raise ValueError(
                f"{familia}: interfaz {meta.get('interfaz')!r}. Este envoltorio solo "
                "sirve modelos que predigan fila a fila.")

        ruta = Path(cfg["artefacto"])
        if not ruta.exists():
            raise FileNotFoundError(
                f"{familia}: no esta {ruta}. Es justo lo que echaba en falta "
                "`modelos_equipo.py`; lo genera `run_pipeline.py`.")
        self.modelo = joblib.load(ruta)
        if not hasattr(self.modelo, "named_steps"):
            raise ValueError(
                f"{familia}: el artefacto es un {type(self.modelo).__name__} suelto, no "
                "un Pipeline con el StandardScaler dentro. Serviria numeros plausibles y "
                "equivocados sobre la matriz cruda. Reentrena con run_pipeline.py.")

    def __repr__(self):
        return f"{self.familia}__s{self.semilla}"


def registrar() -> list[str]:
    """Anade los lineales a `INVENTARIO` y devuelve los que se han podido cargar.

    OJO CON EL ENSEMBLE. `modelos_equipo.cargar("todos")` recorre INVENTARIO, asi que
    registrar aqui cambia de quien es la media que `run_diario` escribe como
    `ensemble11`. Ese numero esta en la memoria. Si se registran estos dos, la media
    pasa a ser de trece y hay que llamarla `ensemble13` y decirlo, no dejar que el
    nombre viejo signifique otra cosa.
    """
    disponibles = []
    for nombre, cfg in MIOS.items():
        if not Path(cfg["artefacto"]).exists():
            print(f"  {nombre}: sin modelo.joblib, no se registra", file=sys.stderr)
            continue
        INVENTARIO[nombre] = cfg
        disponibles.append(nombre)
    return disponibles


def evaluar(matriz="produccion", tramo="va", desde=None, hasta=None):
    """MAE de los lineales sobre el mismo tramo y el mismo real que usa el equipo.

    No sustituye al leaderboard: ahi manda `evaluar_modelos.py` con el simulador de
    arbitraje compartido. Esto solo responde a "¿el artefacto sirve el mismo numero
    que el CSV que entregue?", que es la pregunta de produccion.
    """
    from preparar_tensores import preparar

    T = preparar(matriz, verbose=False)
    m = {"tr": T.tr, "va": T.va, "te": T.te}[tramo]
    f = pd.to_datetime(T.fechas)
    if desde is not None:
        m = m & np.asarray(f >= pd.Timestamp(desde))
    if hasta is not None:
        m = m & np.asarray(f <= pd.Timestamp(hasta))
    real = T.y[m]

    filas = []
    for nombre in MIOS:
        try:
            a = LinealPlano(nombre, 0, matriz)
            p = a.predecir(T, m)
            nota = ""
            mae = float(np.abs(p - real).mean())
        except Exception as e:                                  # noqa: BLE001
            mae, nota = np.nan, str(e).split("\n")[0][:70]
        filas.append({"modelo": nombre, "autor": "Samuel", "MAE": mae, "nota": nota})
    naive = float(np.abs(T.naive[m] - real).mean())
    return pd.DataFrame(filas), naive, int(m.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--evaluar", action="store_true")
    ap.add_argument("--matriz", default="produccion")
    ap.add_argument("--tramo", default="va", choices=["tr", "va", "te"])
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    a = ap.parse_args()

    if a.listar:
        print(f"\n  {'modelo':20s} {'ver':>4s} {'feats':>6s}  artefacto")
        print("  " + "-" * 76)
        for n, c in MIOS.items():
            r = Path(c["artefacto"])
            if Path(c["meta"]).exists():
                meta = json.loads(Path(c["meta"]).read_text(encoding="utf-8"))
                nf, ver = len(meta[c["clave_features"]]), meta.get("version", "?")
            else:
                nf, ver = 0, "?"
            print(f"  {n:20s} {ver:>4s} {nf:6d}  "
                  f"{r.relative_to(REPO)}{'' if r.exists() else '   NO ESTA'}")
        return

    if a.evaluar:
        d, naive, n = evaluar(a.matriz, a.tramo, a.desde, a.hasta)
        print(f"\n  MAE sobre {a.tramo} · {n} dias · matriz {a.matriz}")
        for r in d.sort_values("MAE").itertuples():
            v = ("%8.3f" % r.MAE) if r.MAE == r.MAE else "       -"
            print(f"  {r.modelo:20s} {v}   {r.nota}")
        print(f"  {'naive (precio de D)':20s} {naive:8.3f}")
        return

    print("  usa --listar o --evaluar; para registrar en produccion, "
          "importa `registrar()` desde guardar_predicciones.")


if __name__ == "__main__":
    main()
