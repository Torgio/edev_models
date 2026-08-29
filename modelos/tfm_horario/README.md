# Pipeline horario (ex `feature_selector_horario.ipynb`)

El notebook partido en modulos ejecutables en servidor. Mismos calculos, misma
seleccion de features y mismas fronteras de split; lo que cambia es el envoltorio.

## Estructura

```
run_pipeline.py            # CLI: orquesta las etapas
requirements.txt
tfm_horario/
  ajustes.py               # rutas, fechas del split, umbrales, hiperparametros
                           # (NO es el config.py de credenciales del servidor)
  data.py                  # carga + split + tratamiento + escalado    (celdas 3-4, 16-24, 43)
  selection.py             # Spearman + SFS + colinealidad             (celdas 6, 10, 13)
  preparacion.py           # encadena datos -> seleccion -> tratamiento
  evaluation.py            # naive t-24 + MAE / RMSE / rMAE + tablas   (celdas 53, 55, 59)
  artifacts.py             # cache en disco de las etapas caras
  models/                  # un modelo por fichero, cada uno ejecutable
    sarima.py              # SARIMA sin exogenas (+ motor compartido)  (celdas 26-31)
    sarimax.py             # SARIMA + exogenas                         (celdas 33-39)
    ridge.py               # lineal L2                                 (celdas 42-45)
    elasticnet.py          # lineal L1 + L2                            (celdas 48-49)
```

Guia de despliegue paso a paso en el servidor: ver `DEPLOY.md`.

## Instalacion

Añade al `requirements.txt` del servidor:

```
scikit-learn>=1.3
joblib>=1.3
statsmodels>=0.14      # solo SARIMA/SARIMAX
pmdarima>=2.0          # solo SARIMA/SARIMAX
```

`numpy`, `pandas` y `scipy` ya suelen estar. `matplotlib` no hace falta.

Copia el paquete al lado de `construir_dataset_horario.py`. En el servidor:
`/home/ubuntu/scripts/modelos/`. Con esa estructura no hace falta configurar nada:
`config.py` deduce donde esta `construir_dataset_horario.py` (el directorio padre
del paquete) y escribe en `modelos/salidas/`.

Esto sustituye al `sys.path.append("C:/Users/Powan/Desktop/...")` del notebook, que
en un servidor Linux no funciona. Si algun dia quieres separar codigo y salidas:

```bash
export TFM_OUTPUT_DIR=/var/lib/tfm/salidas
```

## Uso

Cada modelo se ejecuta solo y prepara sus propios datos (carga, split, Spearman +
SFS, tratamiento y, si toca, escalado):

```bash
python -m tfm_horario.models.elasticnet
python -m tfm_horario.models.ridge --forzar
python -m tfm_horario.models.sarimax --estrategia bloques24
```

Tambien valen como script suelto, sin `-m`:

```bash
python tfm_horario/models/elasticnet.py
```

Al terminar, cada uno imprime y guarda sus metricas frente al baseline naive, de
modo que una ejecucion individual ya dice si el modelo lo bate (rMAE < 1).

El orquestador es opcional; solo encadena los cuatro y saca la tabla conjunta:

```bash
python run_pipeline.py                        # los cuatro + comparativa
python run_pipeline.py --modelos ridge elasticnet
python run_pipeline.py --solo-evaluacion      # rehace la tabla con lo ya guardado
```

`--forzar` rehace el tratamiento (y, en SARIMA/SARIMAX, la busqueda de orden). Sin
el, todo se lee de `salidas/artifacts/`: el primer modelo que corra paga el SFS y
los tres siguientes lo reutilizan en segundos.

Los `python -m` hay que lanzarlos desde `/home/ubuntu/scripts/modelos`. Desde otro
directorio, usa la ruta absoluta del fichero (`python .../models/ridge.py`), que
funciona desde cualquier sitio.

Salidas en `modelos/salidas/`: `metricas/*.csv`, `predicciones/*.csv`,
`artifacts/*.pkl`, `logs/*.log`.

### Prueba de solo un modelo lineal

`sarima.py` y `sarimax.py` se importan de forma perezosa, asi que probar solo
Ridge o ElasticNet no necesita `pmdarima` ni `statsmodels` instalados:

```bash
python -m tfm_horario.models.elasticnet
```

Ejecucion desatendida:

```bash
0 3 * * 1 cd /opt/tfm && .venv/bin/python run_pipeline.py >> /var/log/tfm.log 2>&1
```

### Convivencia con el `config.py` del servidor

`modelos/config.py` (vuestro) carga credenciales; `tfm_horario/ajustes.py` (este
paquete) guarda hiperparametros. Nombres distintos a proposito. No hay conflicto
de imports: `construir_dataset_horario` sigue haciendo `from config import
load_config` y este paquete usa imports relativos.

## Decisiones al partir el notebook

- **Celdas alternativas → parametros.** El notebook tenia tres celdas que
  calculaban `predictions` de tres formas distintas (walk-forward hora a hora,
  forecast directo, bloques de 24h) y dos para `predictions_sarimax`; la que
  quedaba viva dependia del orden en que hubieras ejecutado. Ahora es
  `sarima.predecir(..., estrategia=...)` con `walkforward` / `directo` /
  `bloques24`, y la elegida queda escrita en el log.
- **`aplicar_tratamiento` → clase.** Era una funcion que leia globales
  (`selected_sfs`, `cols_a_dropear`, `cols_imputar`, `cols_con_missing`) definidas
  a mano en celdas anteriores. Ahora es `TratamientoHorario`, que se ajusta con
  train, guarda la receta dentro y se serializa: el dia que se abra el test recibe
  exactamente el mismo preprocesado, sin depender del estado del kernel.
- **Orden de definicion.** Las celdas 46 y 50 llamaban a `calcular_metricas` y
  `predictions_dict`, definidos en las celdas 54-55: solo funcionaban si habias
  ejecutado fuera de orden. En la etapa `evaluacion` el orden es correcto.
- **Test sellado.** `data.cargar_splits(incluir_test=True)` lanza excepcion antes
  del 31-ago-2026, para que un cron no lo abra por accidente.
- **`print` → `logging`.** Los graficos del notebook (`plots.py`) se han
  eliminado: el diagnostico que daban los heatmaps de colinealidad se conserva
  como texto en el log (`pares_mas_correlados` y `resumen_colinealidad`). Con eso
  fuera, `matplotlib` deja de ser dependencia.
- **Cada modelo es ejecutable.** La preparacion comun vive en `preparacion.py`, no
  dentro del orquestador, asi que `python -m tfm_horario.models.ridge` funciona
  sin `run_pipeline.py`. Los cuatro comparten la misma cache, asi que lanzarlos
  por separado no repite el SFS.
- **Un modelo por fichero.** `models/` tiene exactamente cuatro: `sarima`,
  `sarimax`, `ridge`, `elasticnet`. Dos piezas que estaban ahi se movieron para
  que sea asi sin duplicar codigo:
  - el baseline naive (t-24) esta en `evaluation.py`, porque no se entrena: es la
    referencia contra la que se miden los cuatro (el denominador del rMAE);
  - `escalar` (StandardScaler) esta en `data.py`, porque es tratamiento de datos y
    lo comparten Ridge y ElasticNet -- un scaler por modelo seria el mismo ajuste
    hecho dos veces, y un sitio mas donde equivocarse y ajustarlo sobre validation.

  `sarimax.py` no duplica el motor: reutiliza `buscar_orden`, `ajustar` y
  `predecir` de `sarima.py` (es el mismo `SARIMAX` de statsmodels, solo cambia que
  `exog` es obligatorio) y añade validaciones que impiden llamarlo sin exogenas o
  con el indice desalineado.

## Un punto a revisar antes de lanzar

En `data.py`, dentro de `TratamientoHorario.fit`, se conserva tal cual la
condicion de la celda 18 del notebook:

```python
cols_a_dropear = [c for c in high_missing.index if corr_abs.get(c, 0) > UMBRAL_CORR]
```

El comentario original decia que se dropean las columnas con mucho missing **y
correlacion debil** con el target, pero la condicion escrita dropea las de
correlacion **fuerte** (`>` en vez de `<`), y deja para imputar precisamente las
debiles. No lo he cambiado para no alterar tus resultados sin avisar. Si la
intencion era la del comentario, invierte el operador y relanza con
`--etapa tratamiento --forzar`.

## Nota si ya habias ejecutado la version anterior

La estructura ha cambiado varias veces: `preprocessing.py` se fundio en `data.py`,
`models/` paso a cuatro ficheros ejecutables, `plots.py` desaparecio y la
preparacion comun se movio a `preparacion.py`. `run_pipeline.py` ya no usa
`--etapa`. Los artifacts viejos pueden no deserializarse: si vienes de una version
anterior, borra `salidas/artifacts/` y relanza.
