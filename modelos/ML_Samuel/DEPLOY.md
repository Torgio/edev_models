# Puesta en marcha en el servidor

Pipeline horario del TFM, adaptado a los requisitos de `Prod.txt` (cierre de
modelos antes del domingo 30).

---

## Estructura

```
edev_models/
|- data/gold/matriz_nucleo.csv        <- LA ENTRADA (133 columnas, 0 nulos)
`- modelos/
   `- ML_samuel/                      <- este proyecto
      |- run_pipeline.py
      |- tfm_horario/                 <- el paquete
      |- entregables/<modelo_id>/     <- LO QUE VA AL PR
      |  |- modelo.joblib
      |  |- pred_val_2025.csv
      |  `- metadata.json
      `- salidas/                     <- artifacts y logs, NO van al PR (.gitignore)
```

No hay que exportar ninguna variable: `ajustes.py` busca `data/gold/matriz_nucleo.csv`
subiendo desde el proyecto. Si la matriz esta en otro sitio:
`export TFM_RUTA_MATRIZ=/ruta/a/matriz_nucleo.csv`.

Ya no hay conexion a Postgres: la entrada es el CSV. `config.py` (credenciales) y
`construir_dataset_horario.py` dejan de usarse en este pipeline.

---

## 1. Subir el codigo

```bash
scp -r tfm_horario run_pipeline.py .gitignore \
    ubuntu@servidor:/home/ubuntu/edev_models/modelos/ML_samuel/
```

**Los dos `config`.** `modelos/config.py` es el vuestro (credenciales) y no se
toca. Los ajustes de este paquete estan en `tfm_horario/ajustes.py`, con ese nombre
para que no se confundan.

---

## 2. Dependencias

Al `requirements.txt` del servidor:

```
scikit-learn>=1.3
joblib>=1.3
statsmodels>=0.14      # solo SARIMA/SARIMAX
pmdarima>=2.0          # solo SARIMA/SARIMAX
```

`numpy`, `pandas` y `scipy` ya deberian estar. `matplotlib` no hace falta.

---

## 3. Comprobar la matriz antes de entrenar

```bash
cd /home/ubuntu/edev_models/modelos/ML_samuel
python -c "
from tfm_horario import ajustes, data
ajustes.configurar_logging('check')
df = data.cargar_dataset(); print(df.shape)"
```

Valida las tres promesas de Nucleo.txt: 133 columnas, 0 nulos y rejilla horaria sin
huecos. Ademas compara el corte de Prod.txt con la columna `split` de la matriz y
avisa si discrepan.

---

## 4. Entrenar

```bash
python -m tfm_horario.models.elasticnet     # el primero paga el SFS
python -m tfm_horario.models.ridge          # reutiliza la cache: segundos
nohup python -m tfm_horario.models.sarima  > sarima.out 2>&1 &
nohup python -m tfm_horario.models.sarimax > sarimax.out 2>&1 &
```

O los cuatro de una: `python run_pipeline.py`.

### Modos de seleccion de features

Cualquier modelo acepta `--seleccion`:

| Modo | Que hace | Coste | Features (aprox.) |
|---|---|---|---|
| `ambos` (por defecto) | Spearman y luego SFS | alto | ~25 |
| `spearman` | solo filtro de correlacion | segundos | ~119 |
| `sfs` | solo seleccion secuencial, sobre las 128 | **el mas alto** | 25 |
| `ninguna` | sin seleccion | cero | 128 |

```bash
python -m tfm_horario.models.elasticnet --seleccion ninguna
python -m tfm_horario.models.ridge --seleccion spearman
python run_pipeline.py --seleccion ambos spearman ninguna   # los tres seguidos
```

Cada modo cachea aparte (`datos_<modo>.pkl`) y escribe su propio entregable:
`elasticnet_horario` para el modo por defecto y `elasticnet_horario_<modo>` para el
resto, de forma que no se pisan y el revisor ve con que seleccion se entreno cada
uno.

`ninguna` es la referencia util: dice cuanto aporta realmente seleccionar. `sfs` a
secas es el mas caro de los cuatro, porque el SFS arranca con las 128 columnas sin
que Spearman haya descartado antes las redundantes.

Cada modelo deja su entregable completo en `entregables/<modelo_id>/`. En el log,
lo primero que hay que mirar es la linea `FILTRO DE FUGA`: dice que columnas se han
descartado por el aviso 3 de Prod.txt.

Los SARIMA van con `nohup` porque `auto_arima` con m=24 puede tardar horas.

---

## 5. Verificar antes del PR

```bash
python run_pipeline.py --solo-verificar
```

Comprueba, para los cuatro: que estan los tres ficheros, que el CSV tiene 8760
filas y que la primera y ultima hora son `2025-01-01T00:00:00Z` y
`2025-12-31T23:00:00Z`. Si algo falla, devuelve codigo 1 y lo dice.

Repasa a mano el `metadata.json` antes de subir: `autor` (se pone con
`TFM_AUTOR` o editando `ajustes.py`) y sobre todo `features_dudosas`.

---

## 6. PR

Sube solo `entregables/`. `salidas/` esta en el `.gitignore`: son artifacts de
varios MB que no aportan nada a la revision.

---

## Notas

**No se calculan metricas.** Prod.txt lo pide expresamente: el MAE y la captura de
arbitraje los calcula el evaluador central sobre los 12 modelos con un unico
script. La linea `control interno` del log es solo un aviso de que la prediccion
no es constante ni tiene NaN; no es una metrica y no viaja en el PR.

**Sin imputacion.** La matriz llega con 0 nulos, asi que ya no hay interpolacion ni
bfill/ffill: si aparece un NaN el pipeline PARA con un error que dice en que columna.
Taparlo escondería un cambio aguas arriba en `depurar_matriz.py`.

**Test 2026 sellado.** `cargar_splits(incluir_test=True)` lanza excepcion antes del
31-ago-2026, y ningun script lo pide.

**Si el SFS tarda demasiado**, las palancas estan en `tfm_horario/ajustes.py`:

| Cambio | Efecto | Altera la seleccion |
|---|---|---|
| `SFS_N_FEATURES` mas bajo (ya en 25) | lineal en k | si |
| `SFS_RF_PARAMS["n_estimators"] = 100` | x2.0 | no |
| `SFS_N_SPLITS` (ya en 3) | x1.6 | no |
| `SPEARMAN_COLLINEARITY_THRESHOLD` mas bajo (ya en 0.85) | mucho | si |
| `SFS_TOL = 1e-3` | x1.8 | si |

Ojo con la escala nueva: la matriz trae 128 features y ~119 pasan Spearman. Con
`SFS_N_FEATURES="auto"` (= 60) serian 5.370 evaluaciones x 3 folds = mas de 16.000
ajustes de random forest, o sea dias en un VPS. Por eso el valor por defecto es 25.

Cuantos cores tiene la maquina condiciona esto: `nproc`.
