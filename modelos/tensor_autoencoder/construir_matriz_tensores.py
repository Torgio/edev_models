"""
Une los embeddings meteorológicos (32 columnas, salida combinada de ERA5-pseudo +
ECMWF real) a una COPIA de matriz_nucleo — NUNCA la matriz original que usa
producción (predecir.py lee directo de matriz_nucleo.parquet).

Unión por merge_asof(direction="backward") sobre 'ts': para cada fila de la
matriz (grano horario), usa el embedding más reciente CONOCIDO hasta ese
momento, nunca uno posterior — misma política de causalidad fijada desde el
inicio del proyecto (punto 5 del contexto original).

TOLERANCIA: 3 horas. El pseudo-tensor y el real garantizan como mucho 3h entre
marcas consecutivas dentro de su rango de cobertura — si una fila de la matriz
no encuentra ningún embedding dentro de esa ventana hacia atrás, queda con NaN
en las 32 columnas nuevas. Esto es intencional: significa que esa fecha está
fuera de TODA cobertura (ni real ni pseudo la cubre, ej. 2026 por ahora) — se
prefiere un NaN explícito y visible a rellenar silenciosamente con un dato
viejo y potencialmente engañoso.

Salida: data/gold/matriz_nucleo_tensores.parquet — copia separada.
"""

import pandas as pd

MATRIZ_ORIGINAL = "matriz_nucleo.parquet"  # ajustar ruta según donde se corra
EMBEDDINGS = "embeddings_meteo_combinado.parquet"
SALIDA = "matriz_nucleo_tensores.parquet"
TOLERANCIA = pd.Timedelta("3h")
PREFIJO_COLUMNAS = "tensor_emb_"

# Actualizacion (notas 49/50 de la memoria del equipo, verificado dos veces
# con metodos independientes -- auditoria contra la fuente y ablacion del
# modelo): es_esios_D/pt_entsoe_D NO son fuga, son el precio ya cerrado del
# dia anterior (informacion legitima). La decision de neutralizarlas como
# feature (sin sacarlas del archivo) para esta comparacion especifica del
# embedding meteorologico vive ahora en preparar_con_embeddings.py -- barato
# de alternar (con/sin) sin reconstruir la matriz. Por eso aca NO se excluye
# ninguna columna: ambas quedan siempre presentes en el archivo.
COLUMNAS_EXCLUIR = []


def unir(matriz_path=MATRIZ_ORIGINAL, embeddings_path=EMBEDDINGS, salida_path=SALIDA,
         tolerancia=TOLERANCIA, columnas_excluir=None, verbose=True):
    columnas_excluir = COLUMNAS_EXCLUIR if columnas_excluir is None else columnas_excluir

    matriz = pd.read_parquet(matriz_path)

    presentes = [c for c in columnas_excluir if c in matriz.columns]
    ausentes = [c for c in columnas_excluir if c not in matriz.columns]
    if presentes:
        matriz = matriz.drop(columns=presentes)
    if verbose:
        if presentes:
            print(f"Columnas excluidas (casi-copia del target, ver COLUMNAS_EXCLUIR): {presentes}")
        if ausentes:
            print(f"AVISO: columnas a excluir no encontradas en la matriz (¿nombre cambió?): {ausentes}")

    emb = pd.read_parquet(embeddings_path)

    # .astype("datetime64[ns]") explicito ademas de pd.to_datetime(): pandas
    # reciente distingue datetime64[ns] de datetime64[us] como tipos DISTINTOS
    # para merge_asof -- si la matriz original (parquet de produccion) y los
    # embeddings (convertidos desde strings ISO) quedan en precisiones
    # distintas, merge_asof rechaza el merge aunque representen lo mismo.
    matriz["ts"] = pd.to_datetime(matriz["ts"]).astype("datetime64[ns]")
    emb["ts"] = pd.to_datetime(emb["ts"]).astype("datetime64[ns]")

    # merge_asof exige ambos lados ordenados por la clave de union
    matriz = matriz.sort_values("ts").reset_index(drop=True)
    emb = emb.sort_values("ts").reset_index(drop=True)

    emb_cols_originales = [c for c in emb.columns if c != "ts"]
    emb = emb.rename(columns={c: f"{PREFIJO_COLUMNAS}{c.split('_')[-1]}" for c in emb_cols_originales})
    emb_cols = [c for c in emb.columns if c != "ts"]

    combinado = pd.merge_asof(
        matriz, emb, on="ts", direction="backward", tolerance=tolerancia
    )

    faltantes = combinado[emb_cols[0]].isna()

    if verbose:
        print(f"Matriz original: {len(matriz)} filas, {len(matriz.columns)} columnas")
        print(f"Embeddings disponibles: {len(emb)} filas")
        print(f"Matriz resultante: {len(combinado)} filas, {len(combinado.columns)} columnas "
              f"(+{len(emb_cols)} nuevas)")
        print(f"\nFilas SIN embedding (fuera de tolerancia de {tolerancia}): "
              f"{faltantes.sum()} de {len(combinado)} ({faltantes.mean()*100:.1f}%)")
        if "split" in combinado.columns:
            print("\nCobertura por split:")
            resumen = combinado.groupby("split")[emb_cols[0]].apply(
                lambda s: (~s.isna()).mean() * 100
            )
            for split, pct in resumen.items():
                print(f"  {split}: {pct:.1f}% con embedding")

    combinado.to_parquet(salida_path, index=False)
    if verbose:
        print(f"\nGuardado en {salida_path}")
    return combinado


if __name__ == "__main__":
    unir()
