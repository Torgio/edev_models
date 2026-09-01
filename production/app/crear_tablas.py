"""Ejecutar `production/app/sql/crear_tablas_bess.sql` sin necesitar el cliente `psql`.

En Windows no hay `psql` instalado, pero si `psycopg2` y las credenciales de `ingesta/config`,
que es como se creo la tabla `predictions`. Esto lee el fichero SQL y lo ejecuta entero en una
sola transaccion: o se crea todo, o no se crea nada.

Todo el DDL es `CREATE TABLE IF NOT EXISTS`, asi que volver a ejecutarlo no rompe nada ni
borra datos. La vista si se reemplaza (`CREATE OR REPLACE VIEW`).

    python production/app/crear_tablas.py --ver              # que hay, sin tocar nada
    python production/app/crear_tablas.py --borrar-viejas   # retira las de la 1a version
    python production/app/crear_tablas.py --crear
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# `production/app/x.py` -> el repo esta dos niveles arriba. Los MOTORES viven en `scripts/`
# y no se duplican aqui: los comparten los notebooks, y dos copias acabarian divergiendo.
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))
sys.path.append(str(Path(__file__).resolve().parent))   # los hermanos de esta carpeta
sys.path.append(str(REPO / "production" / "curva"))     # `generar_curva`, que publica
                                                        # el artefacto que aqui se lee

SQL = Path(__file__).resolve().parent / "sql" / "crear_tablas_bess.sql"
# Todas con el prefijo `app_`: conviven con la veintena de tablas del pipeline de datos
# (`spot_price`, `esios_gen`, `predictions`...) y sin prefijo no se distingue de un vistazo
# cual es de la aplicacion y cual del pipeline.
TABLAS = ["app_user", "app_battery_model", "app_consump_inst", "app_gen_inst",
          "app_consump_shape", "app_gen_shape", "app_curve", "app_curve_hourly",
          "app_study_case", "app_case_run", "app_case_result_annual", "app_case_dispatch"]
VISTA = "app_case_summary"

# Los nombres SIN prefijo de la primera version, para poder retirarlos con `--borrar-viejas`.
VIEJAS = [t[4:] for t in TABLAS if t != "app_user"]


def conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def ver(con):
    with con.cursor() as cur:
        print(f"\n  {'tabla':22s} {'existe':>7s} {'filas':>9s}  columnas")
        print("  " + "-" * 58)
        for t in TABLAS:
            cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
            if cur.fetchone()[0] is None:
                print(f"  {t:22s} {'no':>7s}")
                continue
            cur.execute(f"SELECT count(*) FROM {t}")
            n = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM information_schema.columns
                           WHERE table_name = %s""", (t,))
            c = cur.fetchone()[0]
            print(f"  {t:22s} {'si':>7s} {n:9,d}  {c}")
        cur.execute("SELECT to_regclass(%s)", (f"public.{VISTA}",))
        print(f"\n  vista {VISTA}: {'si' if cur.fetchone()[0] else 'no'}")


def crear(con):
    sql = SQL.read_text(encoding="utf-8")
    print(f"  {SQL.relative_to(REPO)} · {len(sql.splitlines())} lineas")
    with con.cursor() as cur:
        cur.execute(sql)          # una sola transaccion: o todo, o nada
    con.commit()
    print("  ejecutado y confirmado")


def borrar_viejas(con, forzar=False):
    """Retira las tablas de la primera version, las que iban sin prefijo.

    Se niega si alguna tiene filas, salvo `--forzar`. Borrar tablas es irreversible y no
    hay motivo para hacerlo a ciegas: si tienen datos, es que la migracion no era trivial.
    """
    with con.cursor() as cur:
        hay, con_filas = [], []
        for t in VIEJAS + ["case_summary"]:
            cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
            if cur.fetchone()[0] is None:
                continue
            hay.append(t)
            if t != "case_summary":
                cur.execute(f"SELECT count(*) FROM {t}")
                n = cur.fetchone()[0]
                if n:
                    con_filas.append((t, n))
        if not hay:
            print("  no queda ninguna tabla sin prefijo")
            return
        print(f"  se van a borrar {len(hay)}: {', '.join(hay)}")
        if con_filas and not forzar:
            print("\n  ME NIEGO: estas tienen filas y borrarlas es irreversible.")
            for t, n in con_filas:
                print(f"    {t}: {n:,} filas")
            print("  Si de verdad quieres perderlas, repite con --forzar.")
            raise SystemExit(1)
        cur.execute("DROP VIEW IF EXISTS case_summary CASCADE")
        for t in reversed(VIEJAS):
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    con.commit()
    print("  borradas")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--crear", action="store_true")
    ap.add_argument("--ver", action="store_true")
    ap.add_argument("--borrar-viejas", action="store_true",
                    help="retira las tablas de la primera version, las que iban sin app_")
    ap.add_argument("--forzar", action="store_true",
                    help="borrar aunque tengan filas")
    a = ap.parse_args()
    if not SQL.exists():
        raise SystemExit(f"no encuentro {SQL}")
    con = conexion()
    try:
        if a.borrar_viejas:
            borrar_viejas(con, a.forzar)
        if a.crear:
            crear(con)
        ver(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
