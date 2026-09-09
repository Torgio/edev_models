# Puesta en marcha en el servidor

Pipeline horario del TFM. Cubre dos cosas distintas que conviene no mezclar:

- **Entrenar y entregar** (secciones 1-6): produce los tres ficheros que pide
  `Prod.txt` y que consume el evaluador central. No toca Postgres.
- **Servir en produccion** (seccion 7): engancha los modelos a `run_diario.py`, que
  es el cron que ya corre en el servidor. Ahi si hay base de datos, y no es codigo
  nuestro.

---

## Estructura

```
edev_models/
|- data/gold/matriz_nucleo.csv        <- LA ENTRADA (133 columnas, 0 nulos)
|- scripts/
|  |- modelos_equipo.py               <- de Torgio: el registro de modelos servibles
|  |- run_diario.py                   <- de Torgio: la pasada de las 11:15
|  `- modelos_samuel.py               <- NUESTRO envoltorio de produccion (seccion 7)
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

`modelos_samuel.py` es la unica pieza que **no** vive bajo `ML_samuel/`, y no es
opcional: calcula la raiz del repo con `Path(__file__).parent.parent` e importa
`modelos_equipo`, igual que `predecir.py` y `run_diario.py`. Desde otra carpeta
apunta a rutas que no existen.

No hay que exportar ninguna variable: `ajustes.py` busca `data/gold/matriz_nucleo.csv`
subiendo desde el proyecto. Si la matriz esta en otro sitio:
`export TFM_RUTA_MATRIZ=/ruta/a/matriz_nucleo.csv`.

El entrenamiento no se conecta a Postgres: la entrada es el CSV. `config.py`
(credenciales) y `construir_dataset_horario.py` no se usan aqui. En produccion si
hay base de datos, pero la abre `run_diario.py`, no nosotros.

**Mayusculas.** El servidor es Linux y la carpeta es `ML_samuel`, con `s`
minuscula (la de Magdalena es `ML_Magui`, con `M`). `modelos_samuel.py` la escribe
asi; si renombras, cambia tambien `MIOS` o fallara solo en el servidor.

---

## 1. Subir el codigo

```bash
scp -r tfm_horario run_pipeline.py .gitignore \
    ubuntu@servidor:/home/ubuntu/edev_models/modelos/ML_samuel/
scp modelos_samuel.py ubuntu@servidor:/home/ubuntu/edev_models/scripts/
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

El `requirements.txt` del repo viene congelado del venv de ingesta y no lleva nada
de esto, asi que hay que anadirlo antes de la primera pasada. La version de
`scikit-learn` importa: un pickle guardado con una version y cargado con otra puede
fallar en silencio, y la version real con la que se entreno queda escrita en
`metadata.json["libreria"]`.

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
python -m tfm_horario.models.ridge
python -m tfm_horario.models.elasticnet
nohup python -m tfm_horario.models.sarima  > sarima.out 2>&1 &
nohup python -m tfm_horario.models.sarimax > sarimax.out 2>&1 &
```

O los cuatro de una: `python run_pipeline.py`.

Con el modo por defecto no hay SFS, asi que la preparacion tarda segundos y da igual
cual corra primero. Los SARIMA van con `nohup` porque `auto_arima` con m=24 puede
tardar horas.

### Modos de seleccion de features

| Modo | Que hace | Coste | Features (aprox.) |
|---|---|---|---|
| `spearman` **(por defecto)** | solo filtro de correlacion | segundos | ~119 |
| `ambos` | Spearman y luego SFS | alto | ~25 |
| `sfs` | solo seleccion secuencial, sobre las 128 | **el mas alto** | 25 |
| `ninguna` | sin seleccion | cero | 128 |

**El modo por defecto es `spearman` y no se cambia.** No es una preferencia: es la
identidad de los cuatro entregables. `entrega.id_con_modo` le da el id base al modo
por defecto (`ridge_horario`) y sufijo a los demas (`ridge_horario_ambos`), asi que
mover `MODO_SELECCION` convertiria los cuatro modelos del leaderboard en otros
cuatro con el mismo nombre, y las metricas ya publicadas dejarian de corresponder al
artefacto que hay en el servidor. `ajustes.py` lanza un `RuntimeError` al importarse
si alguien lo toca.

Los demas modos son para comparar en la memoria, y se piden asi:

```bash
python -m tfm_horario.models.ridge --seleccion ambos
python run_pipeline.py --seleccion spearman ambos ninguna   # los tres seguidos
```

Cada modo cachea aparte (`datos_<modo>.pkl`) y escribe su propio entregable, de
forma que no se pisan y el revisor ve con que seleccion se entreno cada uno.

`ninguna` es la referencia util: dice cuanto aporta realmente seleccionar. `sfs` a
secas es el mas caro de los cuatro, porque el SFS arranca con las 128 columnas sin
que Spearman haya descartado antes las redundantes.

En el log, lo primero que hay que mirar es la linea `FRONTERA:`: dice que columnas
se han descartado por el aviso 3 de Prod.txt (por defecto, ninguna).

---

## 5. Verificar antes del PR

```bash
python run_pipeline.py --solo-verificar
```

Comprueba, para los cuatro: que estan los tres ficheros, que el CSV tiene 8760 filas
con la primera y ultima hora en `2025-01-01T00:00:00Z` y `2025-12-31T23:00:00Z`, y
que el `metadata.json` trae `version`, `artefacto` y `seleccion` — sin ellos el
modelo no se puede dar de alta en produccion. Si algo falla, devuelve codigo 1.

Ademas, al guardar cada entregable se ejecuta sola una comprobacion que no es de
formato sino de contrato: **se recarga el `modelo.joblib` y se le pide que reproduzca
el CSV a partir de las features CRUDAS**. Es la unica que detecta el fallo que de
verdad duele: un estimador lineal guardado sin su escalador dentro no falla al
predecir sobre la matriz cruda, devuelve numeros del orden de magnitud equivocado.
Por eso el artefacto de Ridge y ElasticNet es un `Pipeline` con el `StandardScaler`
dentro y no el estimador pelado.

Repasa a mano el `metadata.json` antes de subir: `autor` (se pone con `TFM_AUTOR` o
editando `ajustes.py`) y sobre todo `features_dudosas`.

---

## 6. PR

Sube `entregables/` y, si vas a produccion, `scripts/modelos_samuel.py`. `salidas/`
esta en el `.gitignore`: son artifacts de varios MB que no aportan a la revision.

---

## 7. Servir en produccion

Solo para los modelos que pasen el corte del leaderboard. El resto se queda en
entregable y no toca el servidor.

**No hay `predict.py`.** El contrato del capitulo 5 de `artifact_prod.docx`
(`predecir(fecha_objetivo, ctx)`) describe un orquestador que en este repo no
existe. Lo instalado es `run_diario.py`, que no importa modulos por `modelo_id`:
llama a `guardar_predicciones.produccion(..., equipo=True)` y los modelos entran por
`modelos_equipo.INVENTARIO` con la firma de `predecir.Miembro`:

```
predecir(T, m) -> np.ndarray (dias, 24) en EUR/MWh, alineado con T.fechas[m]
```

`scripts/modelos_samuel.py` es el envoltorio contra ese contrato. Hereda
`ArbolPlano` de Torgio y solo cambia el cargador, para no reimplementar el manejo de
los cambios de hora (marzo pierde la hora 2, octubre la repite).

```bash
python scripts/modelos_samuel.py --listar      # que hay y si el joblib esta
python scripts/modelos_samuel.py --evaluar     # MAE sobre validacion, mismo real
```

Para registrarlos, `modelos_samuel.registrar()` los inyecta en `INVENTARIO`.

**Tres avisos antes de hacerlo:**

- **Solo entran Ridge y ElasticNet.** Predicen fila a fila, sin estado. SARIMA y
  SARIMAX no: un `SARIMAXResults` no predice desde una fila, continua una serie, y
  hay que extenderle el estado con el precio real dia a dia (lo que hacia
  `bloques24` en validacion). `predecir(T, m)` recibe dias sueltos sin garantia de
  orden, asi que meterlos ahi daria numeros peores que los medidos, en silencio.
- **El ensemble cambia de tamano.** `modelos_equipo.cargar("todos")` recorre
  `INVENTARIO`, asi que registrar dos modelos convierte el `ensemble11` que escribe
  `run_diario` en una media de trece. Ese numero esta en la memoria: o se renombra a
  `ensemble13` y se dice, o los lineales se sirven fuera de la media.
- **La PK de `predictions` es `(datetime, model)`, sin `source`.** Un INSERT de
  produccion sobre un dia que ya tiene backfill de test no crea fila nueva: le cambia
  el `source` y el backfill se pierde. `run_diario.choque()` lo protege salvo que se
  use `--forzar`.

---

## Notas

**No se calculan metricas.** Prod.txt lo pide expresamente: el MAE y la captura de
arbitraje los calcula el evaluador central sobre los 12 modelos con un unico script.
La linea `control interno` del log es solo un aviso de que la prediccion no es
constante ni tiene NaN; no es una metrica y no viaja en el PR.

**Sin imputacion.** La matriz llega con 0 nulos, asi que ya no hay interpolacion ni
bfill/ffill: si aparece un NaN el pipeline PARA con un error que dice en que columna.
Taparlo esconderia un cambio aguas arriba en `depurar_matriz.py`.

**ElasticNet aborta si el ajuste final no converge.** Antes solo se comprobaba la
convergencia durante el tuneo; el reajuste final podia quedarse a medias y sus
coeficientes dependen de donde se corto la optimizacion, o sea que no es
reproducible. Si pasa, sube `EN_MAX_ITER` en `ajustes.py` y relanza.

**Test 2026 sellado.** `cargar_splits(incluir_test=True)` lanza excepcion antes del
31-ago-2026, y ningun script lo pide.

**Si el SFS tarda demasiado** (solo aplica a los modos `ambos` y `sfs`), las palancas
estan en `tfm_horario/ajustes.py`:

| Cambio | Efecto | Altera la seleccion |
|---|---|---|
| `SFS_N_FEATURES` mas bajo (ya en 25) | lineal en k | si |
| `SFS_RF_PARAMS["n_estimators"] = 100` | x2.0 | no |
| `SFS_N_SPLITS` (ya en 3) | x1.6 | no |
| `SPEARMAN_COLLINEARITY_THRESHOLD` mas bajo (ya en 0.85) | mucho | si |
| `SFS_TOL = 1e-3` | x1.8 | si |

Con `SFS_N_FEATURES="auto"` (= 60) serian 5.370 evaluaciones x 3 folds = mas de
16.000 ajustes de random forest, o sea dias en un VPS. Por eso el valor por defecto
es 25. Cuantos cores tiene la maquina condiciona esto: `nproc`.
