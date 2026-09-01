"""Servir los modelos finales: sueltos o en ensemble.

EL ENSEMBLE NO ES UN FICHERO. Son N modelos y una media, y el promedio ocurre despues de
las N predicciones, asi que no hay forma de fundirlo en un solo `.keras`. Esto es el
envoltorio que hace que se use como si lo fuera:

    from predecir import cargar
    p = cargar("ensemble")                  # o "gru", "seq2seq_absoluto", ...
    y = p.predecir()                        # (dias, 24) en EUR/MWh, el tramo de test
    y = p.predecir(desde="2025-06-01")

Los miembros del ensemble no se guardan en ningun sitio aparte: se derivan de
`por_semilla.csv` con la misma regla que uso el notebook -- el mejor de cada familia
SEGUN VALIDACION. Un fichero mas seria un sitio mas donde desincronizarse.

LA COMPROBACION DE CONTRATO no es opcional. Un `.keras` guarda pesos, no el orden de las
columnas ni la estandarizacion. Si la matriz se regenera con otro orden de canales, el
modelo sigue prediciendo -- con numeros plausibles y equivocados, sin ningun aviso.

Se comprueba el ESPACIO DE ENTRADA, no el hash:

    FALLA   las tres listas de columnas, o los escaladores, no coinciden
    AVISA   el hash de la matriz es otro pero lo demas cuadra

Esa distincion es la que permite que esto funcione a diario: la matriz gana una fila cada
dia y su hash cambia siempre, asi que un contrato que abortara por hash se romperia el
primer dia. Los escaladores se ajustan solo sobre train y el corte va por fecha fija
(train hasta 2024-12-31), asi que anadir dias nuevos no los mueve.

Uso por terminal:
    python scripts/predecir.py                          # ensemble sobre el test
    python scripts/predecir.py --modelo gru
    python scripts/predecir.py --listar
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

SEMILLA = 42

# Los modelos servibles viven aparte de los resultados del entrenamiento: en
# `production/models` solo estan los 8 representantes y sus preprocesados, sin las 16
# semillas perdedoras ni las predicciones ya calculadas. Si no existe, se cae a la
# carpeta de entrenamiento, que tiene los mismos ficheros entre otros muchos.
MODELOS = REPO / "production" / "models"


def _carpeta(matriz):
    if MODELOS.is_dir() and (MODELOS / "por_semilla.csv").exists():
        return MODELOS
    return REPO / "data" / "gold" / f"finales_{matriz}"


class ContratoRoto(RuntimeError):
    """La matriz de hoy no es aquella con la que se entreno el modelo."""


def _comprobar(pre, T, quien, avisar=True):
    """Compara lo que el modelo espera contra lo que la matriz trae.

    El HASH no puede ser motivo de fallo. Cambia cada vez que la matriz gana un dia, que
    en produccion es todos los dias: un contrato que aborte por hash se rompe el primer
    dia y obliga a reentrenar por nada. Lo que de verdad tiene que coincidir es el ESPACIO
    DE ENTRADA -- el orden de las columnas y los escaladores -- porque eso es lo que el
    modelo aprendio. Los escaladores se ajustan solo sobre train, y el corte va por fecha
    fija, asi que anadir dias nuevos no los mueve: si han cambiado, es que algo mas lo hizo.
    """
    fallos, avisos = [], []
    h_mod, h_hoy = pre.get("hash_matriz"), T.meta.get("hash")
    if h_mod != h_hoy:
        avisos.append(f"la matriz no es la del entrenamiento ({h_mod} -> {h_hoy}); "
                      f"si solo se le han anadido dias, es lo esperado")

    for clave, esc in (pre.get("escaladores") or {}).items():
        v = T.esc.get(clave)
        if v is None:
            continue
        for cual in ("mu", "sd"):
            a = np.asarray(esc[cual], dtype=float).ravel()
            b = np.asarray(getattr(v, cual), dtype=float).ravel()
            if a.shape != b.shape:
                fallos.append(f"escalador {clave}.{cual}: {a.size} valores frente a {b.size}")
            elif not np.allclose(a, b, rtol=1e-4, atol=1e-6):
                i = int(np.argmax(np.abs(a - b)))
                fallos.append(f"escalador {clave}.{cual} cambiado: canal {i} pasa de "
                              f"{a[i]:.4f} a {b[i]:.4f}. El modelo recibiria la entrada en "
                              f"otra escala y devolveria numeros plausibles y equivocados")

    for campo, actual in (("canales", T.canales), ("cols_dec", T.cols_dec),
                          ("cols_est", T.cols_est)):
        esperado = list(pre.get(campo, []))
        if esperado != list(actual):
            if len(esperado) != len(actual):
                fallos.append(f"{campo}: esperaba {len(esperado)} columnas, hay {len(actual)}")
            else:
                dif = [(i, a, b) for i, (a, b) in enumerate(zip(esperado, actual)) if a != b]
                fallos.append(f"{campo}: {len(dif)} columnas cambiadas de sitio, "
                              f"la primera en la posicion {dif[0][0]} "
                              f"({dif[0][1]} -> {dif[0][2]})")
    if fallos:
        raise ContratoRoto(
            f"{quien} no casa con esta matriz:\n  - " + "\n  - ".join(fallos) +
            "\n  Reentrena, o usa la matriz con la que se entreno. Predecir igualmente "
            "devolveria numeros plausibles y equivocados.")
    if avisar and avisos:
        for a in avisos:
            print(f"  aviso · {quien}: {a}", file=sys.stderr)


class Miembro:
    """Un modelo entrenado con todo lo que hace falta para volver a usarlo."""

    def __init__(self, familia, semilla, carpeta):
        self.familia, self.semilla = familia, semilla
        self.pre = json.loads((carpeta / f"{familia}.preprocesado.json")
                              .read_text(encoding="utf-8"))
        self._avisado = False
        self.absoluto = self.pre.get("objetivo") == "absoluto"
        self.plana = self.pre.get("entrada") == "plana"
        s = semilla - SEMILLA
        if familia == "boosting":
            from entrenar_finales import BosqueHorario
            self.modelo = BosqueHorario.cargar(carpeta / f"{familia}__s{s}")
        else:
            from tensorflow import keras
            self.modelo = keras.models.load_model(carpeta / f"{familia}__s{s}.keras")

    def __repr__(self):
        return f"{self.familia}__s{self.semilla - SEMILLA}"

    def predecir(self, T, m):
        _comprobar(self.pre, T, str(self), avisar=not self._avisado)
        self._avisado = True
        if self.plana:
            from entrenar_finales import vista_plana
            X = vista_plana(T)[m]
        else:
            X = T.ent(m)
        p = self.modelo.predict(X, verbose=0)
        d = self.pre["destipificar"]
        y = np.asarray(p) * d["sd"] + d["mu"]
        # el residuo se predice CONTRA el naive: hay que devolverselo para volver a EUR/MWh
        return y if self.absoluto else y + T.naive[m]


class Predictor:
    """Uno o varios miembros. Con varios, la prediccion es la media de todos."""

    def __init__(self, nombre, miembros, matriz):
        self.nombre, self.miembros, self.matriz = nombre, miembros, matriz
        self._T = None

    def tensores(self):
        if self._T is None:
            from preparar_tensores import preparar
            self._T = preparar(self.matriz, verbose=False)
        return self._T

    def predecir(self, desde=None, hasta=None, tramo="te", detalle=False):
        """Devuelve un DataFrame de (dias x 24 h) en EUR/MWh.

        `tramo` acota a train/val/test; `desde` y `hasta` filtran por fecha dentro de el.
        """
        T = self.tensores()
        m = {"tr": T.tr, "va": T.va, "te": T.te, "todo": np.ones(len(T.fechas), bool)}[tramo]
        f = pd.to_datetime(T.fechas)
        # comparar un DatetimeIndex ya devuelve un ndarray de bool: ponerle .values
        # reventaba con AttributeError en cuanto se filtraba por fecha.
        if desde is not None:
            m = m & np.asarray(f >= pd.Timestamp(desde))
        if hasta is not None:
            m = m & np.asarray(f <= pd.Timestamp(hasta))
        if not m.any():
            raise ValueError(f"ningun dia en el tramo '{tramo}' con ese rango de fechas")

        cada = {str(mb): mb.predecir(T, m) for mb in self.miembros}
        media = np.mean(list(cada.values()), axis=0)
        cols = [f"h{h:02d}" for h in range(24)]
        idx = pd.to_datetime(T.fechas[m])
        if detalle:
            return {k: pd.DataFrame(v, index=idx, columns=cols) for k, v in cada.items()}
        return pd.DataFrame(media, index=idx, columns=cols)

    def __repr__(self):
        return f"<{self.nombre}: {len(self.miembros)} miembro(s) — " \
               f"{', '.join(str(m) for m in self.miembros)}>"


def _representantes(carpeta):
    """El mejor de cada familia SEGUN VALIDACION -- la misma regla que el notebook."""
    d = pd.read_csv(carpeta / "por_semilla.csv")
    return d.loc[d.groupby("familia").MAE_val.idxmin()].sort_values("MAE_val")


def cargar(modelo="ensemble", matriz="nucleo", semilla=None):
    """`modelo` es una familia, "ensemble", "equipo" o "ensemble_equipo".

    Los dos ultimos traen los arboles planos de Magdalena y Willy (ver
    `scripts/modelos_equipo.py`). `ensemble` se deja como estaba -- nuestras 8 familias --
    a proposito: es el numero que esta escrito en la memoria y no debe moverse solo porque
    aparezcan modelos nuevos.
    """
    carpeta = _carpeta(matriz)
    if not (carpeta / "por_semilla.csv").exists():
        raise FileNotFoundError(f"no hay resultados en {carpeta}: entrena primero")
    rep = _representantes(carpeta)

    externos = []
    if modelo in ("equipo", "ensemble_equipo"):
        from modelos_equipo import representantes
        externos = representantes(matriz)
        if modelo == "equipo":
            return Predictor(modelo, externos, matriz)

    if modelo in ("ensemble", "ensemble_equipo"):
        filas = list(rep.itertuples())
    else:
        cand = rep[rep.familia == modelo]
        if cand.empty:
            raise ValueError(f"'{modelo}' no esta entre {sorted(rep.familia)}")
        filas = list(cand.itertuples())
    if semilla is not None:
        filas = [f._replace(semilla=semilla) for f in filas]

    miembros, sin_fichero = [], []
    for r in filas:
        try:
            miembros.append(Miembro(r.familia, int(r.semilla), carpeta))
        except FileNotFoundError as e:
            sin_fichero.append(f"{r.familia}__s{int(r.semilla) - SEMILLA}: {e}")
    if sin_fichero:
        # Callarse aqui daria un ensemble mas pequeno del que dice la memoria, con otro
        # MAE, y sin que nadie lo note.
        raise FileNotFoundError(
            f"faltan modelos en disco:\n  - " + "\n  - ".join(sin_fichero) +
            "\n  Reentrena esas familias con --guardar-modelos.")
    return Predictor(modelo, miembros + externos, matriz)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--modelo", default="ensemble",
                    help="una familia, 'ensemble' (nuestras 8), 'equipo' "
                         "(los arboles de Magdalena y Willy) o "
                         "'ensemble_equipo' (los 11)")
    ap.add_argument("--matriz", default="nucleo")
    ap.add_argument("--tramo", default="te", choices=["tr", "va", "te", "todo"])
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    ap.add_argument("--guardar", help="ruta de un CSV donde escribir la prediccion")
    ap.add_argument("--listar", action="store_true", help="que hay disponible")
    a = ap.parse_args()

    carpeta = _carpeta(a.matriz)
    if a.listar:
        rep = _representantes(carpeta)
        print(f"\nmatriz {a.matriz} · representante de cada familia (por MAE de validacion)\n")
        print(f"leyendo de {carpeta}\n")
        print(f"  {'familia':18s} {'sem':>4s} {'val':>7s} {'test':>7s}  modelo en disco")
        for r in rep.itertuples():
            s = r.semilla - SEMILLA
            p = (carpeta / f"{r.familia}__s{s}")
            hay = (p.with_suffix(".keras").exists() or
                   (p.is_dir() and len(list(p.glob("h*.txt"))) == 24))
            print(f"  {r.familia:18s} s{s:<3d} {r.MAE_val:7.3f} {r.MAE_test:7.3f}  "
                  f"{'si' if hay else 'NO -- reentrenar'}")
        return

    p = cargar(a.modelo, a.matriz)
    print(p)
    y = p.predecir(desde=a.desde, hasta=a.hasta, tramo=a.tramo)
    print(f"\n{len(y)} dias x 24 h · media {y.values.mean():.2f} EUR/MWh "
          f"· min {y.values.min():.2f} · max {y.values.max():.2f}")
    print(y.head().round(2).to_string())
    if a.guardar:
        y.to_csv(a.guardar)
        print(f"\nguardado en {a.guardar}")


if __name__ == "__main__":
    main()
