r"""
TFM Energia UCM - Indexar la documentacion para busqueda semantica (RAG documental) (30-ago-2026)

Esto SI es RAG clasico: trocea `docs/notas_memoria_tfm.md` y `docs/columnas_pendientes_equipo.md`
en unidades semanticas (cada nota numerada "## N. Titulo" ya es un chunk coherente -- no hace
falta partir por tamaño de texto), genera un embedding por chunk con un modelo LOCAL (sin clave de
API adicional: `fastembed`, modelo multilingue `paraphrase-multilingual-MiniLM-L12-v2`, 384
dimensiones) y los guarda en Postgres con `pgvector` (extension ya instalada, verificado
29-ago-2026).

Por que embeddings locales y no una API: la clave de Anthropic no cubre embeddings (Anthropic
recomienda Voyage AI, una cuenta aparte) -- para no sumar una segunda dependencia de pago a un
prototipo, se usa un modelo pequeño que corre en la propia maquina. Si mas adelante se quiere
mejor calidad de busqueda, cambiar a Voyage es sustituir esta funcion de embedding, no rehacer el
diseño.

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/asistente/indexar_documentacion.py
"""

import re
import sys
from pathlib import Path

import psycopg2
from fastembed import TextEmbedding
from pgvector.psycopg2 import register_vector

REPO = Path(__file__).parent.parent.parent
sys.path.append(str(REPO / "ingesta"))

MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONES = 384

FUENTES = [
    ("notas_memoria_tfm", REPO / "docs" / "notas_memoria_tfm.md"),
    ("columnas_pendientes_equipo", REPO / "docs" / "columnas_pendientes_equipo.md"),
]


def _conectar():
    from config import load_config
    _, db_config = load_config()
    return psycopg2.connect(**db_config)


def _trocear(texto: str) -> list[dict]:
    """Parte un documento en sus notas numeradas ('## N. Titulo'), cada una un chunk completo.
    El preambulo antes de la primera nota (si lo hay) se guarda como chunk 0."""
    partes = re.split(r"^## (\d+)\. (.+)$", texto, flags=re.MULTILINE)
    chunks = []
    if partes[0].strip():
        chunks.append({"numero": 0, "titulo": "(preámbulo)", "texto": partes[0].strip()})
    # tras el split, partes queda como [preambulo, num1, titulo1, cuerpo1, num2, titulo2, cuerpo2, ...]
    for i in range(1, len(partes), 3):
        numero, titulo, cuerpo = int(partes[i]), partes[i + 1], partes[i + 2]
        chunks.append({"numero": numero, "titulo": titulo.strip(), "texto": cuerpo.strip()})
    return chunks


def main():
    print(f"Cargando modelo de embeddings local ({MODELO_EMBEDDING})...")
    modelo = TextEmbedding(model_name=MODELO_EMBEDDING)

    todos_los_chunks = []
    for fuente, ruta in FUENTES:
        if not ruta.exists():
            print(f"  [aviso] {ruta} no existe, se omite")
            continue
        texto = ruta.read_text(encoding="utf-8")
        chunks = _trocear(texto)
        for c in chunks:
            c["fuente"] = fuente
        todos_los_chunks.extend(chunks)
        print(f"  {fuente}: {len(chunks)} chunks")

    print(f"\nGenerando {len(todos_los_chunks)} embeddings...")
    textos = [f"{c['titulo']}\n\n{c['texto']}" for c in todos_los_chunks]
    embeddings = list(modelo.embed(textos))

    print("Conectando a Postgres y creando la tabla (si no existe)...")
    conn = _conectar()
    register_vector(conn)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS documentacion_embeddings (
            id SERIAL PRIMARY KEY,
            fuente TEXT NOT NULL,
            numero INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            texto TEXT NOT NULL,
            embedding VECTOR({DIMENSIONES}) NOT NULL,
            UNIQUE (fuente, numero)
        )
    """)
    conn.commit()

    print("Insertando/actualizando chunks (upsert por fuente+numero, para poder re-indexar sin duplicar)...")
    for c, emb in zip(todos_los_chunks, embeddings):
        cur.execute("""
            INSERT INTO documentacion_embeddings (fuente, numero, titulo, texto, embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fuente, numero) DO UPDATE SET
                titulo = EXCLUDED.titulo, texto = EXCLUDED.texto, embedding = EXCLUDED.embedding
        """, (c["fuente"], c["numero"], c["titulo"], c["texto"], emb))
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM documentacion_embeddings")
    total = cur.fetchone()[0]
    conn.close()
    print(f"\nListo: {total} chunks indexados en la tabla documentacion_embeddings.")


if __name__ == "__main__":
    main()
