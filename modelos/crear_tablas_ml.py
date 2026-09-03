"""Crea las tablas de modelizacion y produccion en tfm_energia.

Ejecuta sql/crear_tablas_ml.sql y despues comprueba que quedo lo que tenia que
quedar. Es idempotente: se puede lanzar varias veces sin romper nada, porque el
DDL usa CREATE TABLE IF NOT EXISTS.

    python modelos/crear_tablas_ml.py            # crea y verifica
    python modelos/crear_tablas_ml.py --verificar  # solo mira, no toca nada
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO / "ingesta"))
from config import load_config          # noqa: E402

DDL = REPO / "sql" / "crear_tablas_ml.sql"
TABLAS = ["predictions", "models", "model_metrics", "model_metrics_daily",
          "bess_plan", "bess_result"]
# `predictions` la crea scripts/guardar_predicciones.py --crear-tabla; aqui solo se comprueba.
# Del primer diseño. `bess_plan` esta aqui aunque el nombre se repita: la version
# antigua tenia otras columnas (fecha_objetivo, hora, version, run_ts) y CREATE TABLE
# IF NOT EXISTS no la cambiaria -- se quedaria la vieja en silencio.
ANTIGUAS = ["ml_predicciones", "ml_modelos", "ml_metricas", "bess_resultado", "bess_plan"]


def motor():
    _, db = load_config()
    return create_engine(
        f"postgresql+psycopg2://{db['user']}:{db['password']}"
        f"@{db['host']}:{db['port']}/{db['dbname']}")


def es_nueva(con, tabla):
    """bess_plan existe en los dos diseños. La nueva se reconoce por tener `datetime`
    (la vieja usaba fecha_objetivo + hora), asi que no hay que avisar de ella."""
    if tabla not in TABLAS:
        return False
    return bool(con.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = 'datetime'"), {"t": tabla}).scalar())


def verificar(con):
    print(f"\n{'tabla':22s} {'existe':8s} {'columnas':>9s} {'filas':>8s}")
    print("-" * 52)
    for t in TABLAS:
        hay = con.execute(text(
            "SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar()
        if not hay:
            print(f"{t:22s} {'NO':8s} {'-':>9s} {'-':>8s}")
            continue
        cols = con.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = :t"), {"t": t}).scalar()
        filas = con.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        print(f"{t:22s} {'si':8s} {cols:>9d} {filas:>8d}")
    sobra = []
    for t in ANTIGUAS:
        hay = con.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar()
        if hay and not es_nueva(con, t):
            n = con.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            sobra.append((t, n))
    if sobra:
        print("\n  tablas del primer diseño que ya no se usan "
              "(las predicciones van en `predictions`):")
        for t, n in sobra:
            print(f"    {t:22s} {n} filas"
                  f"{'  -> se puede borrar' if n == 0 else '  <-- TIENE DATOS, mirar antes'}")
        print("    para quitarlas:  python modelos/crear_tablas_ml.py --limpiar")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verificar", action="store_true", help="no ejecuta el DDL")
    ap.add_argument("--limpiar", action="store_true",
                    help="borra las tablas del primer diseño, SOLO si estan vacias")
    a = ap.parse_args()

    eng = motor()

    if not a.verificar:
        # El DDL se ejecuta con el cursor crudo de psycopg2, NO con text(): text()
        # trata los ":" como parametros -- y el DDL lleva colons dentro del JSON de
        # ejemplo del comentario -- y ademas no ejecuta scripts de varias sentencias.
        print(f"ejecutando {DDL.relative_to(REPO)} ...")
        cruda = eng.raw_connection()
        try:
            with cruda.cursor() as cur:
                cur.execute(DDL.read_text(encoding="utf-8"))
            cruda.commit()
            print("DDL aplicado")
        except Exception:
            cruda.rollback()
            raise
        finally:
            cruda.close()

    if a.limpiar:
        with eng.begin() as con:
            for t in ANTIGUAS:
                hay = con.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar()
                if not hay:
                    continue
                if es_nueva(con, t):
                    continue
                n = con.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                if n:
                    print(f"  {t}: {n} filas -- NO se borra, revisala a mano")
                else:
                    con.execute(text("DROP VIEW IF EXISTS v_ultima_prediccion"))
                    con.execute(text(f"DROP TABLE {t}"))
                    print(f"  {t}: borrada (estaba vacia)")

    with eng.connect() as con:
        bd = con.execute(text("SELECT current_database()")).scalar()
        print(f"conectado a {bd}")
        verificar(con)


if __name__ == "__main__":
    main()
