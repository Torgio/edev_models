import pandas as pd

real = pd.read_parquet('embeddings_ecmwf_real.parquet')
pseudo = pd.read_parquet('embeddings_pseudo_completo.parquet')

print(f'Real: {len(real)} filas')
print(f'Pseudo: {len(pseudo)} filas')

solapados = set(real['ts']) & set(pseudo['ts'])
print(f'Timestamps solapados (real gana, se descarta el pseudo ahi): {len(solapados)}')

pseudo_filtrado = pseudo[~pseudo['ts'].isin(solapados)]
combinado = pd.concat([real, pseudo_filtrado], ignore_index=True).sort_values('ts').reset_index(drop=True)

assert combinado['ts'].is_unique, 'Quedaron ts duplicados -- revisar antes de seguir'
print(f'Combinado final: {len(combinado)} filas, sin duplicados')

combinado.to_csv('embeddings_meteo_combinado.csv', index=False)
combinado.to_parquet('embeddings_meteo_combinado.parquet', index=False)
