# Puesta en marcha en el servidor

Guia de despliegue del pipeline horario. Asume Linux con Python 3.10+ y acceso a
la BBDD desde la maquina.

---

## 1. Subir el codigo

Todo vive en `/home/ubuntu/scripts/modelos/`, con el paquete AL LADO de
`construir_dataset_horario.py`:

```
/home/ubuntu/scripts/modelos/
|- construir_dataset_horario.py
|- bronzeDF_pipeline.py
|- config.py               <- el vuestro: credenciales (load_config)
|- credentials.json        <- no se sube a git
|- requirements.txt        <- el vuestro, con las lineas añadidas del paso 2
|- run_pipeline.py
|- tfm_horario/            <- el paquete (sus ajustes estan en ajustes.py)
`- salidas/                <- se crea solo en la primera ejecucion
```

```bash
scp -r tfm_horario run_pipeline.py ubuntu@servidor:/home/ubuntu/scripts/modelos/
ssh ubuntu@servidor
cd /home/ubuntu/scripts/modelos && ls
```

Dentro de `tfm_horario/` tiene que estar `__init__.py`: sin el, los `python -m`
fallan.

**Los dos `config`.** `modelos/config.py` es el vuestro (credenciales) y no se
toca. Los ajustes de este paquete (fechas del split, umbrales, hiperparametros)
estan en `tfm_horario/ajustes.py`, con ese nombre precisamente para que nadie los
confunda. No se pisan: `construir_dataset_horario` hace `from config import
load_config` y sigue leyendo el vuestro; el paquete usa `from . import ajustes`.

---

## 2. Dependencias

Añade estas lineas al `requirements.txt` que ya teneis:

```
scikit-learn>=1.3
joblib>=1.3
statsmodels>=0.14
pmdarima>=2.0
```

`numpy`, `pandas` y `scipy` ya deberian estar (los usa el resto del proyecto);
comprueba y añade solo lo que falte. `matplotlib` NO hace falta: este pipeline no
genera graficos.

Las dos ultimas, `statsmodels` y `pmdarima`, son SOLO para SARIMA y SARIMAX.
`pmdarima` compila C y es la que mas problemas da en un servidor limpio, asi que
si quieres validar primero el pipeline con los modelos lineales, instala sin ella:
Ridge y ElasticNet no la importan (los dos SARIMA se cargan de forma perezosa).

```bash
cd /home/ubuntu/scripts/modelos
pip install -r requirements.txt
python -c "import pandas, sklearn, scipy, joblib; print('ok')"
```

---

## 3. Variables de entorno: NINGUNA

Con la estructura del paso 1 no hay que exportar nada. `config.py` deduce
`MODELOS_DIR` como el directorio padre del paquete
(`/home/ubuntu/scripts/modelos/`), que es exactamente donde esta
`construir_dataset_horario.py`, y escribe las salidas en
`/home/ubuntu/scripts/modelos/salidas/` (`artifacts/`, `metricas/`,
`predicciones/`, `logs/` se crean solos).

Solo si algun dia quieres separar codigo y salidas:

```bash
export TFM_OUTPUT_DIR=/var/lib/tfm/salidas
```

**Como lanzar los scripts.** `python -m` resuelve el paquete desde el directorio
actual, asi que hay que estar en `/home/ubuntu/scripts/modelos`:

```bash
cd /home/ubuntu/scripts/modelos
python -m tfm_horario.models.elasticnet
```

Desde otro directorio da `ModuleNotFoundError: No module named 'tfm_horario'`. Si
prefieres no depender del `cd` (util en cron), usa la ruta absoluta del fichero:
funciona desde cualquier sitio, porque cada script localiza el paquete por su
propia ubicacion.

```bash
/home/ubuntu/scripts/modelos/.venv/bin/python \
    /home/ubuntu/scripts/modelos/tfm_horario/models/elasticnet.py
```

---

## 4. Probar la conexion a la BBDD antes de entrenar

Es el punto que mas falla (credenciales, firewall, VPN) y conviene descubrirlo
antes de arrancar una etapa larga:

```bash
python -c "
from tfm_horario import data
df = data.cargar_dataset()
print(df.shape)
print(df.index.min(), '->', df.index.max())
"
```

Si esto imprime dimensiones y rango de fechas, el resto es cuesta abajo. Si falla
por import, revisa `TFM_MODELOS_DIR`; si falla por conexion, es `bronzeDF_pipeline`
y sus credenciales, no este paquete.

---

## 5. Primera ejecucion: un modelo lineal

```bash
python -m tfm_horario.models.elasticnet
```

Esta primera pasada hace TODO el camino: carga, split, Spearman, SFS,
tratamiento, escalado y entrenamiento. El SFS es lo caro (entrena
O(n_features^2) x n_splits random forests). Si tarda demasiado, baja
`SFS_MAX_FILAS` en `config.py` para la primera prueba y subelo despues.

Al terminar imprime la tabla con su MAE, RMSE y rMAE frente al naive.

---

## 6. Revisar las salidas

```bash
cd /home/ubuntu/scripts/modelos
ls salidas/artifacts/     # splits, spearman, sfs, tratamiento, datos_tratados, pred_*, modelo_*
cat salidas/metricas/metricas_elasticnet.csv
tail -40 salidas/logs/elasticnet.log
```

Que mirar en el log:
- `Spearman: N de M features conservadas` y `SFS: N de M`
- `se dropean (n): [...]` y las columnas imputadas
- `NaN restantes en train: 0` (si no es 0, hay algo que revisar)
- `rMAE` < 1 significa que bate al naive

Lo importante de esta etapa: `artifacts/sfs.pkl` ya existe, asi que los tres
modelos siguientes NO repiten la seleccion.

---

## 7. Los demas modelos

Ridge reutiliza la cache y tarda segundos:

```bash
python -m tfm_horario.models.ridge
```

Para SARIMA/SARIMAX hacen falta `statsmodels` y `pmdarima`:

```bash
nohup python -m tfm_horario.models.sarima  > sarima.out 2>&1 &
nohup python -m tfm_horario.models.sarimax > sarimax.out 2>&1 &
```

Van con `nohup` (o `tmux`) porque `auto_arima` con m=24 sobre decenas de miles de
horas puede tardar horas: si se cae la sesion SSH, el proceso se lleva por delante
el trabajo. El orden encontrado se cachea (`orden_sarima.pkl`), asi que probar otra
estrategia despues es rapido:

```bash
python -m tfm_horario.models.sarima --estrategia directo
```

---

## 8. Comparativa final y automatizacion

Con los cuatro entrenados, la tabla conjunta:

```bash
python run_pipeline.py --solo-evaluacion
cat salidas/metricas/metricas_validation.csv
```

Salen tambien `predicciones_validation.csv` (real + las cuatro predicciones) y
`detalle_validation.csv` (error absoluto hora a hora), que son los ficheros que
alimentan las tablas de la memoria.

Ejecucion desatendida semanal:

```cron
0 3 * * 1 cd /home/ubuntu/scripts/modelos && .venv/bin/python run_pipeline.py >> salidas/logs/cron.log 2>&1
```

El `cd` es necesario aqui porque `run_pipeline.py` importa el paquete; cron
arranca en `$HOME` y con otro PATH, de ahi la ruta completa al python del venv.

---

## Notas

**Reejecuciones.** Sin `--forzar` todo se lee de `artifacts/`. Con `--forzar` se
rehace el tratamiento (y la busqueda de orden en SARIMA/SARIMAX), pero NO la
seleccion. Para rehacer el SFS, borra `artifacts/sfs.pkl` y `artifacts/spearman.pkl`.

**Test sellado.** `data.cargar_splits(incluir_test=True)` lanza excepcion antes del
31-ago-2026. Ningun script lo pide: todo lo de arriba entrena con train y evalua
con validation. La apertura del test es un acto unico y manual, no algo que deba
hacer un cron.

**Si vienes de una version anterior del codigo**, borra `artifacts/` antes de nada:
la estructura de modulos ha cambiado y los `.pkl` viejos pueden no deserializarse.

**Donde se tocan los hiperparametros.** En `tfm_horario/ajustes.py`: fronteras del
split, umbrales de Spearman, `SFS_MAX_FILAS`, rejillas de Ridge/ElasticNet,
parametros de `auto_arima` y estrategia por defecto de SARIMA/SARIMAX.
