"""Llena ml_modelos y ml_metricas con lo que ya hay en el repo.

De donde sale cada cosa:
  models       <- modelos/ML_Magui/metadata_*.json
                  modelos/ML_Samuel/entregables/*/metadata.json
                  data/gold/finales_nucleo/*.preprocesado.json (+ por_semilla.csv)
  model_metrics<- data_temp/leaderboard_{validation,test}.csv

Usa `model` y `seed`, los mismos nombres que la tabla `predictions` del equipo,
para que el JOIN sea directo. seed = -1 significa "sin semilla" (ensemble, baselines).

Los supuestos del simulador de arbitraje se importan de evaluar_modelos.py y se
guardan JUNTO a cada numero de captura. Es la leccion del 30 de agosto: la captura
no es una metrica unica, asi que el numero viaja con su definicion.

Idempotente: vuelve a ejecutarse sin duplicar (upsert por clave primaria).

    python modelos/registrar_modelos.py            # escribe
    python modelos/registrar_modelos.py --dry-run  # solo enseña lo que haria
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "modelos"))
from config import load_config                                    # noqa: E402
from evaluar_modelos import POTENCIA_MW, CAPACIDAD_MWH, EFICIENCIA  # noqa: E402

SIMULADOR = {"potencia_mw": POTENCIA_MW, "capacidad_mwh": CAPACIDAD_MWH,
             "eficiencia": EFICIENCIA, "ciclos_dia": 1,
             "regla": "carga en las horas mas baratas predichas, descarga en las mas caras"}
GOLD = REPO / "data" / "gold"


SEMILLA_BASE = 42


def partir(nombre: str):
    """'lightgbm__s2' -> ('lightgbm', 44);  'ensemble' -> ('ensemble', -1).

    La semilla se devuelve en su valor real (42+n), igual que la guarda `predictions`,
    no como el indice del fichero.
    """
    if "__s" in nombre:
        base, s = nombre.split("__s")
        return base, SEMILLA_BASE + int(s)
    return nombre, -1


def catalogo() -> list[dict]:
    filas, vistos = [], set()

    def add(**kw):
        clave = (kw["model"], kw["seed"])
        if clave not in vistos:
            vistos.add(clave); filas.append(kw)

    meta_matriz = json.loads((GOLD / "matriz_nucleo.meta.json").read_text(encoding="utf-8"))
    hash_nucleo = meta_matriz.get("hash")

    # --- boosting sobre nucleo (fichas propias, una por semilla)
    for f in sorted((REPO / "modelos" / "ML_Magui").glob("metadata_*.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        for i, semilla in enumerate(m["semillas"]):
            add(model=m["modelo_id"], seed=int(semilla), familia=m["familia"],
                autor=m["autor"], matrix=m["matriz"], matrix_hash=m["hash_matriz"],
                artefacto=m["artefactos"][i], libreria=m["libreria"], python=m["python"],
                entrenado_desde=m["entrenado_desde"], entrenado_hasta=m["entrenado_hasta"],
                features=m["features"],
                features_dudosas=m.get("features_dudosas", []), notas=m.get("notas"))

    # --- bloque estadistico (solo validacion, sin artefacto todavia)
    for f in sorted((REPO / "modelos").glob("**/metadata.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        if "modelo_id" not in m:          # metadata de otra cosa, no de un modelo
            continue
        add(model=m["modelo_id"], seed=-1, familia=m.get("familia", "estadistico"),
            autor=m.get("autor", "Samuel"), matrix="dataset_horario", matrix_hash=None,
            artefacto=None, libreria=m.get("libreria"), python=m.get("python"),
            entrenado_desde=m.get("entrenado_desde"), entrenado_hasta=m.get("entrenado_hasta"),
            features=m.get("features"), features_dudosas=m.get("features_dudosas", []),
            notas="Sin artefacto ni predicciones de test a 30-ago")

    # --- redes y boosting de las redes
    por_semilla = pd.read_csv(GOLD / "finales_nucleo" / "por_semilla.csv")
    for pre in sorted((GOLD / "finales_nucleo").glob("*.preprocesado.json")):
        fam = pre.stem.replace(".preprocesado", "")
        p = json.loads(pre.read_text(encoding="utf-8"))
        cols = sorted({c for k in ("canales", "cols_dec", "cols_est") for c in p.get(k, [])})
        sem = sorted(por_semilla.loc[por_semilla.familia == fam, "semilla"].unique())
        for i, semilla in enumerate(sem):
            ext = "txt" if fam == "boosting" else "keras"
            add(model=fam, seed=int(semilla), familia="boosting" if fam == "boosting" else "rnn",
                autor="Leandro", matrix="nucleo", matrix_hash=p.get("hash_matriz", hash_nucleo),
                artefacto=f"data/gold/finales_nucleo/{fam}__s{i}" + ("" if ext == "txt" else ".keras"),
                libreria=None, python=None,
                entrenado_desde=meta_matriz.get("ventana", " -> ").split(" -> ")[0] or None,
                entrenado_hasta=meta_matriz.get("train_end"),
                features=cols, features_dudosas=[], notas=None)

    # --- derivados y referencias
    add(model="ensemble", seed=-1, familia="ensemble", autor="equipo",
        matrix="nucleo", matrix_hash=hash_nucleo, artefacto=None, libreria=None, python=None,
        entrenado_desde=None, entrenado_hasta=meta_matriz.get("train_end"), features=None, features_dudosas=[],
        notas="No es un fichero: media de los representantes de familia, ver scripts/predecir.py")
    for b, d in [("naive_D1", "el precio de ayer"), ("media_movil_7d", "media de 7 dias, hora a hora")]:
        add(model=b, seed=-1, familia="baseline", autor="referencia", matrix="nucleo",
            matrix_hash=hash_nucleo, artefacto=None, libreria=None, python=None,
            entrenado_desde=None, entrenado_hasta=None, features=None,
            features_dudosas=[], notas=d)
    return filas


def metricas() -> list[dict]:
    filas = []
    for fichero, periodo in [("leaderboard_validation.csv", "val_2025"),
                             ("leaderboard_test.csv", "test_2026")]:
        ruta = REPO / "data_temp" / fichero
        if not ruta.exists():
            print(f"  aviso: falta {fichero}, se salta"); continue
        for _, r in pd.read_csv(ruta).iterrows():
            mid, ver = partir(r["modelo"])
            filas.append(dict(
                model=mid, seed=ver, periodo=periodo, corte="global",
                n_obs=int(r["n_horas"]), mae=r.get("MAE"), rmse=r.get("RMSE"),
                smape=r.get("sMAPE"), captura_pct=r.get("captura_%"),
                eur_dia=r.get("eur_dia"), pico_1h_pct=r.get("pico_1h_%"),
                skill_vs_naive=r.get("skill_%"),
                cobertura_ic80=r.get("cobertura_IC80_%"),
                simulador=json.dumps(SIMULADOR)))
    return filas


SQL_MODELO = text("""
INSERT INTO models (model, seed, familia, autor, matrix, matrix_hash, artefacto,
                    libreria, python, entrenado_desde, entrenado_hasta,
                    features, features_dudosas, notas)
VALUES (:model, :seed, :familia, :autor, :matrix, :matrix_hash, :artefacto,
        :libreria, :python, :entrenado_desde, :entrenado_hasta,
        CAST(:features AS jsonb), CAST(:features_dudosas AS jsonb), :notas)
ON CONFLICT (model, seed) DO UPDATE SET
    familia = EXCLUDED.familia, autor = EXCLUDED.autor, matrix = EXCLUDED.matrix,
    matrix_hash = EXCLUDED.matrix_hash, artefacto = EXCLUDED.artefacto,
    libreria = EXCLUDED.libreria, python = EXCLUDED.python,
    entrenado_desde = EXCLUDED.entrenado_desde, entrenado_hasta = EXCLUDED.entrenado_hasta,
    features = EXCLUDED.features,
    features_dudosas = EXCLUDED.features_dudosas, notas = EXCLUDED.notas
""")

SQL_METRICA = text("""
INSERT INTO model_metrics (model, seed, periodo, corte, n_obs, mae, rmse, smape,
                           cobertura_ic80, captura_pct, eur_dia, pico_1h_pct,
                           skill_vs_naive, simulador)
VALUES (:model, :seed, :periodo, :corte, :n_obs, :mae, :rmse, :smape,
        :cobertura_ic80, :captura_pct, :eur_dia, :pico_1h_pct,
        :skill_vs_naive, CAST(:simulador AS jsonb))
ON CONFLICT (model, seed, periodo, corte) DO UPDATE SET
    n_obs = EXCLUDED.n_obs, mae = EXCLUDED.mae, rmse = EXCLUDED.rmse,
    smape = EXCLUDED.smape, cobertura_ic80 = EXCLUDED.cobertura_ic80,
    captura_pct = EXCLUDED.captura_pct, eur_dia = EXCLUDED.eur_dia,
    pico_1h_pct = EXCLUDED.pico_1h_pct, skill_vs_naive = EXCLUDED.skill_vs_naive,
    simulador = EXCLUDED.simulador, calculado_en = now()
""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    mods, mets = catalogo(), metricas()

    # Toda fila lleva las mismas claves aunque la fuente no las traiga: si falta una,
    # SQLAlchemy revienta a mitad del INSERT con "bind parameter" y es un error tonto
    # de localizar. Mejor un NULL explicito.
    CAMPOS = ["model", "seed", "familia", "autor", "matrix", "matrix_hash", "artefacto",
              "libreria", "python", "entrenado_desde", "entrenado_hasta",
              "features", "features_dudosas", "notas"]
    mods = [{c: m.get(c) for c in CAMPOS} for m in mods]

    for m in mods:
        m["features"] = json.dumps(m["features"]) if m["features"] is not None else None
        m["features_dudosas"] = json.dumps(m.get("features_dudosas") or [])

    print(f"{len(mods)} modelos y {len(mets)} filas de metricas preparadas")
    print("\n  " + ", ".join(sorted({m['model'] for m in mods})))
    if a.dry_run:
        print("\n--dry-run: no se ha escrito nada"); return

    _, db = load_config()
    eng = create_engine(f"postgresql+psycopg2://{db['user']}:{db['password']}"
                        f"@{db['host']}:{db['port']}/{db['dbname']}")
    with eng.begin() as con:
        for m in mods:
            con.execute(SQL_MODELO, m)
        faltan = {(x["model"], x["seed"]) for x in mets} - {(m["model"], m["seed"]) for m in mods}
        if faltan:
            print(f"\n  aviso: {len(faltan)} metricas sin modelo en el catalogo, se omiten: {sorted(faltan)[:6]}")
        for x in mets:
            if (x["model"], x["seed"]) not in faltan:
                con.execute(SQL_METRICA, x)
        n_mod = con.execute(text("SELECT count(*) FROM models")).scalar()
        n_met = con.execute(text("SELECT count(*) FROM model_metrics")).scalar()
    print(f"\nmodels: {n_mod} filas · model_metrics: {n_met} filas")


if __name__ == "__main__":
    main()
