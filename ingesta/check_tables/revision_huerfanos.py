"""
Revision de huerfanos: tablas y columnas que ya no sirven para nada
===================================================================
Busca en la base de datos lo que sobra, y lo cruza con el codigo del repo para
no proponer borrar algo que alguien esta usando.

CRITERIO — una columna es huerfana si cumple LAS DOS cosas:
  1. Esta al 100% NULL, o es constante en toda la serie.
  2. No aparece mencionada en ningun fichero .py ni .sql del repositorio.

La segunda condicion es la importante. Una columna vacia puede estar esperando
a que alguien active su pipeline; si nadie la nombra en el codigo, no espera
nada. Sin ese cruce esto seria una lista de sospechas, no de hechos.

Para las TABLAS el criterio es parecido: vacias o con muy pocas filas, y sin
menciones en el codigo salvo en comentarios que las den por sustituidas.

POR QUE MOLESTARSE
Cada columna muerta es una fila mas en el diccionario de datos, una pregunta
mas en la reunion, y un despiste en potencia para quien llegue nuevo. Limpiar
no cambia ningun resultado, pero evita discusiones futuras.

USO
    python revision_huerfanos.py
    python revision_huerfanos.py --repo ~/git/edev_models
    python revision_huerfanos.py --sql          # imprime el SQL de limpieza

NO BORRA NADA. Solo informa y, con --sql, escribe las sentencias para que las
revise una persona antes de ejecutarlas.
"""

import argparse
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

for ruta in [Path(__file__).parent.parent, Path(__file__).parent, Path("ingesta")]:
    if (ruta / "config.py").exists():
        sys.path.append(str(ruta))
        break
from config import load_config


# Tablas que sabemos sustituidas, con el motivo. Se comprueban aparte.
SUSTITUIDAS = {
    "trayport_daily": "sustituida por trayport_trades + trayport_daily_ohlc",
    "entsoe_data": "partida en entsoe_gen_data + entsoe_load_inter (ago-2026)",
    "esios_marketdata": "partida en esios_gen + esios_load_inter + spot_price (14-ago)",
    "esios_load_inter": "fusionada en load_inter (18-ago)",
    "marketdata_qh": "esquema anterior a agosto de 2026",
}

# Columnas de auditoria: nunca se proponen para borrar aunque esten vacias.
IGNORAR = {"created_at", "updated_at", "id"}


def conectar():
    _, db = load_config()
    return psycopg2.connect(**db)


def q(conn, sql, params=None):
    return pd.read_sql(sql, conn, params=params)


def buscar_en_repo(repo: Path, termino: str) -> int:
    """Cuantas veces aparece el termino en ficheros .py, .sql y .md del repo.
    Se usa grep por velocidad; si no esta disponible, se recorre a mano."""
    try:
        r = subprocess.run(
            ["grep", "-r", "--include=*.py", "--include=*.sql", "--include=*.md",
             "-c", termino, str(repo)],
            capture_output=True, text=True, timeout=60,
        )
        return sum(int(l.rsplit(":", 1)[1]) for l in r.stdout.splitlines()
                   if l.rsplit(":", 1)[-1].isdigit())
    except Exception:
        total = 0
        for p in list(repo.rglob("*.py")) + list(repo.rglob("*.sql")) + list(repo.rglob("*.md")):
            if ".git" in p.parts:
                continue
            try:
                total += p.read_text(encoding="utf-8", errors="ignore").count(termino)
            except Exception:
                pass
        return total


def sec(t):
    print("\n" + "=" * 76)
    print(f"  {t}")
    print("=" * 76)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=None, help="Ruta al repositorio (por defecto, dos niveles arriba)")
    p.add_argument("--sql", action="store_true", help="Imprime el SQL de limpieza")
    a = p.parse_args()

    repo = Path(a.repo).expanduser() if a.repo else Path(__file__).resolve().parent.parent.parent
    if not (repo / "ingesta").exists():
        print(f"AVISO: no parece un repo valido: {repo}")
        print("       Usa --repo para indicar la ruta correcta.\n")

    conn = conectar()
    try:
        tablas = q(conn, """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name
        """)["table_name"].tolist()

        sec("1 · TABLAS SUSTITUIDAS QUE SIGUEN EN LA BASE")
        encontradas = []
        for t in tablas:
            if t not in SUSTITUIDAS:
                continue
            n = q(conn, f'SELECT COUNT(*) c FROM "{t}"').c.iloc[0]
            usos = buscar_en_repo(repo, t)
            encontradas.append({"tabla": t, "filas": n, "menciones_codigo": usos,
                                "motivo": SUSTITUIDAS[t]})
        if encontradas:
            df = pd.DataFrame(encontradas)
            print(df.to_string(index=False))
            print("\n  «menciones_codigo» alto no significa que se use: puede ser el propio")
            print("  comentario que la da por sustituida. Conviene mirarlo antes de borrar.")
        else:
            print("  Ninguna. El esquema esta limpio de tablas sustituidas.")

        sec("2 · COLUMNAS AL 100% NULL")
        vacias = []
        for t in tablas:
            total = q(conn, f'SELECT COUNT(*) c FROM "{t}"').c.iloc[0]
            if total == 0:
                continue
            cols = q(conn, """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%(t)s
                ORDER BY ordinal_position
            """, {"t": t})["column_name"].tolist()
            cols = [c for c in cols if c not in IGNORAR]
            if not cols:
                continue
            expr = ", ".join(f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "{c}"'
                             for c in cols)
            nulos = q(conn, f'SELECT {expr} FROM "{t}"').iloc[0]
            for c in cols:
                if int(nulos[c]) == total:
                    vacias.append({"tabla": t, "columna": c, "filas": total})

        if not vacias:
            print("  Ninguna columna esta completamente vacia.")
        else:
            for v in vacias:
                v["menciones_codigo"] = buscar_en_repo(repo, v["columna"])
            df = pd.DataFrame(vacias).sort_values(["menciones_codigo", "tabla"])
            print(df.to_string(index=False))
            print()
            huerfanas = df[df.menciones_codigo == 0]
            if huerfanas.empty:
                print("  Todas aparecen en el codigo: son columnas pendientes de llenar,")
                print("  no huerfanas. No hay nada que proponer para borrar.")
            else:
                print(f"  HUERFANAS CONFIRMADAS ({len(huerfanas)}): vacias Y sin ninguna")
                print("  mencion en el repositorio. Nadie las escribe ni las lee.")
                for _, r in huerfanas.iterrows():
                    print(f"    - {r.tabla}.{r.columna}")

        sec("3 · COLUMNAS CONSTANTES (un solo valor en toda la serie)")
        print("  Una columna constante no explica nada en un modelo, aunque no este vacia.\n")
        constantes = []
        for t in tablas:
            total = q(conn, f'SELECT COUNT(*) c FROM "{t}"').c.iloc[0]
            if total < 100:
                continue
            cols = q(conn, """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%(t)s
                  AND data_type IN ('double precision','numeric','integer','bigint','real')
            """, {"t": t})["column_name"].tolist()
            cols = [c for c in cols if c not in IGNORAR]
            for c in cols:
                r = q(conn, f'SELECT COUNT(DISTINCT "{c}") d, COUNT("{c}") n FROM "{t}"')
                if r.n.iloc[0] > 100 and r.d.iloc[0] == 1:
                    val = q(conn, f'SELECT "{c}" v FROM "{t}" WHERE "{c}" IS NOT NULL LIMIT 1').v.iloc[0]
                    constantes.append({"tabla": t, "columna": c, "valor": val,
                                       "filas_con_dato": int(r.n.iloc[0])})
        if constantes:
            print(pd.DataFrame(constantes).to_string(index=False))
        else:
            print("  Ninguna.")

        if a.sql:
            sec("4 · SQL DE LIMPIEZA — REVISAR ANTES DE EJECUTAR")
            print("  -- Nada de esto se ejecuta solo. Copiar, revisar y decidir en equipo.\n")
            for e in encontradas:
                print(f'  -- {e["tabla"]}: {e["motivo"]} ({e["filas"]} filas)')
                print(f'  -- DROP TABLE {e["tabla"]};\n')
            if vacias:
                for v in vacias:
                    if v.get("menciones_codigo", 1) == 0:
                        print(f'  -- {v["tabla"]}.{v["columna"]}: 100% NULL y sin menciones en el codigo')
                        print(f'  -- ALTER TABLE {v["tabla"]} DROP COLUMN {v["columna"]};\n')
            print("  Antes de ejecutar cualquiera de estas: comprobar que la tabla no la")
            print("  usa un notebook local de alguien, que no esta en un cron del servidor,")
            print("  y hacer copia de seguridad si tiene datos.")

    finally:
        conn.close()

    print("\n" + "-" * 76)
    print("  Este script NO borra nada. Solo informa.")


if __name__ == "__main__":
    main()
