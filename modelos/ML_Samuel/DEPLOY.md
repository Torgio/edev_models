# Puesta en marcha en el servidor

Pipeline horario del TFM, adaptado a los requisitos de `Prod.txt` (cierre de
modelos antes del domingo 30).

---

## Estructura

```
edev_models/modelos/                  <- aqui viven construir_dataset_horario.py,
|                                        construir_dataset_maestro.py y config.py
`- ML_samuel/                         <- este proyecto
   |- run_pipeline.py
   |- tfm_horario/                    <- el paquete
   |- entregables/<modelo_id>/        <- LO QUE VA AL PR
   |  |- modelo.joblib
   |  |- pred_val_2025.csv
   |  `- metadata.json
   `- salidas/                        <- artifacts y logs, NO van al PR (.gitignore)
```

No hay que exportar ninguna variable: `ajustes.py` busca hacia arriba hasta
encontrar `construir_dataset_horario.py`, asi que localiza `modelos/` solo.

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

## 3. Comprobar la conexion antes de entrenar

```bash
cd /home/ubuntu/edev_models/modelos/ML_samuel
python -c "
from tfm_horario import data
df = data.cargar_dataset(); print(df.shape, df.index.min(), '->', df.index.max())"
```

---

## 4. Entrenar

```bash
python -m tfm_horario.models.elasticnet     # el primero paga el SFS
python -m tfm_horario.models.ridge          # reutiliza la cache: segundos
nohup python -m tfm_horario.models.sarima  > sarima.out 2>&1 &
nohup python -m tfm_horario.models.sarimax > sarimax.out 2>&1 &
```

O los cuatro de una: `python run_pipeline.py`.

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

**Test 2026 sellado.** `cargar_splits(incluir_test=True)` lanza excepcion antes del
31-ago-2026, y ningun script lo pide.

**Si el SFS tarda demasiado**, las palancas estan en `tfm_horario/ajustes.py`:

| Cambio | Efecto | Altera la seleccion |
|---|---|---|
| `SFS_RF_PARAMS["n_estimators"] = 100` | x2.0 | no |
| `SFS_N_SPLITS = 3` | x1.6 | no |
| las dos juntas | x3.2 | no |
| `SFS_TOL = 1e-3` | x1.8 | si |

Cuantos cores tiene la maquina condiciona esto: `nproc`.
