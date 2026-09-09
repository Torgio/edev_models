# Estudio de instalación — integración aislada

Esta vista complementa **BESS → Operación diaria**, sin sustituirlo. Está disponible
en **BESS → Estudio de instalación**. En producción se conecta exclusivamente a
`tfm_energia_test`; la base operativa `tfm_energia` no recibe sus curvas ni estudios.

## Preparación y acceso

En `app/.env.local`, configurar el origen que sirve tanto `/session` como
`/api/bat/*`, y los identificadores de estudios compartidos autorizados:

```dotenv
DASHBOARD_API_URL=https://vps-16d0afbc.vps.ovh.net
BESS_STUDY_RUN_IDS=16
```

Para leer una API BESS aislada por un túnel local sin cambiar dónde se valida la
sesión, configurar además `BESS_STUDY_API_URL=http://127.0.0.1:8011`. Este origen
solo se usa para `/api/bat/*`; `DASHBOARD_API_URL` continúa siendo la autoridad de
autenticación. En desarrollo la función queda habilitada automáticamente.

En Sites se usan estos valores de producción:

```dotenv
BESS_STUDY_ENABLED=1
BESS_STUDY_API_URL=https://vps-16d0afbc.vps.ovh.net/api/bat-test
```

La ruta `/api/bat-test/` de Nginx conserva el login de Pulso, pero se dirige al
servicio local 8011, cuya unidad debe declarar `TFM_TEST_DB_NAME=tfm_energia_test`.
El instalador `production/app/deploy/install_snapshot_test_proxy.sh` verifica esa
condición antes de publicar la ruta y rechaza la instalación si no se cumple.

El estudio 16 es el ejemplo comunicado por el equipo; configurarlo no garantiza
que siga existiendo, que tenga despacho para todas las fechas o que su metodología
económica esté validada. Se pueden habilitar varios IDs separados por comas.

Reiniciar la web tras cambiar la configuración:

```sh
cd /Users/magui/git/edev_models/app
pnpm dev --hostname 127.0.0.1 --port 3000
```

Abrir `http://127.0.0.1:3000/` e iniciar sesión con el usuario habitual de Pulso
del servidor. No usar una credencial creada únicamente para la API local.
Con esta configuración no es necesario arrancar la API local.

## Qué se consulta

- Contexto de la ejecución: ID del caso, fecha, años con resultados y número de días
  históricos/simulados guardados. Los años no se presentan como fechas exactas del período.
- En nuevas ejecuciones con copia de parámetros: nombre del caso e instalaciones,
  fechas exactas, consumo anual, potencia solar y potencia/capacidad de batería.
  En ejecuciones antiguas se indican como no disponibles. Una instalación nula en
  una copia válida significa que no se incluyó en el estudio; no es un dato ausente.
- Indicadores guardados: VAN P10/P50/P90, porcentaje de escenarios con VAN
  positivo, ahorro acumulado del período, ciclos diarios y vida estimada.
- Resultados anuales guardados, con cobertura y origen visibles.
- Despacho horario: consumo, generación, carga, descarga, estado de carga y precio.
- Identificador de ejecución y procedencia de la curva, cuando están guardados.

El navegador no calcula VAN, ahorro ni una estrategia de batería. Solo presenta
resultados y convierte MW a kW y MWh a kWh. Las horas nominales se etiquetan h1–h24,
sin reinterpretarlas como instantes UTC durante los cambios de hora.

El despacho muestra el escenario que devuelve el servidor (por defecto, el primero
guardado), no necesariamente el escenario mediano. El período inicial se elige a
partir de la fecha original de inicio, o de los años del resultado si no hay copia;
puede requerir elegir otra fecha si el despacho
no está guardado para ese tramo. Los errores o datos ausentes no se sustituyen por
simulaciones ni valores de ejemplo.

## Límites y seguridad

El proxy local solo admite GET, verifica la sesión Pulso, restringe los estudios a
la lista configurada y limita los tramos a 31 días. No admite un correo o una
identidad proporcionada desde el formulario. La ruta queda desactivada en
producción y en hosts distintos de loopback.

Esto no sustituye el control de propiedad en el backend. Antes de publicar habrá
que vincular cada estudio e instalación con el usuario autenticado y comprobar esa
propiedad en todas las consultas. Esta prueba es exclusivamente para estudios
compartidos autorizados del equipo.

El VAN se muestra con una advertencia metodológica: está pendiente revisar la
contabilización del coste de ciclo junto con la inversión inicial. No se presenta
como recomendación de inversión ni se recalcula para ocultar esa incertidumbre.

Quedan fuera de esta fase la carga de ficheros, creación/ejecución de estudios,
comparación automática de tamaños, cola de tareas y publicación.

## Comprobación manual

1. Iniciar sesión y abrir la nueva subvista BESS.
2. Confirmar que el ID y las cifras coinciden con el resultado guardado del servidor.
3. Elegir una fecha con despacho y contrastar una hora con la respuesta del API.
4. Cambiar fecha y tramo: no deben quedar gráficos de la consulta anterior.
5. Sin sesión o para un ID no habilitado, no deben devolverse datos.

Las pruebas automatizadas usan respuestas simuladas solo en los tests para
verificar permisos, contrato, unidades y horas; no sustituyen esta comprobación
con una sesión real.

## Copia de parámetros por ejecución (versión 1)

Implementación preparada en local; **no se ha aplicado la migración ni ejecutado un
estudio en la base compartida**. El estudio 16 no se completa retroactivamente.

`production/app/caso.py` copia antes de optimizar los objetos que acaba de leer y
que realmente pasa al motor. `app_case_run.input_snapshot` (JSONB) se inserta en la
misma transacción que el resultado y las series. Si falla el guardado, no se confirma
una ejecución parcial. El script verifica que exista la columna antes de calcular.

Se conservan:

- Caso, nombres/códigos e identificadores de instalación y batería; fechas originales
  y efectivamente evaluadas en calendario nominal.
- Potencia en MW, duración en horas, capacidad en MWh, eficiencia y CAPEX por MWh.
- Consumo anual en MWh, crecimiento, tarifa, generación en MWp y degradación.
- Perfiles normalizados usados al proyectar consumo y generación.
- Parámetros efectivos del optimizador, coste de ciclo, ventana, tasa de descuento
  y OPEX. Esto no cambia ni valida la metodología del VAN.

La copia no contiene correo, user_id, credenciales ni rutas de ficheros. No es una
imagen completa del entorno reproducible: los precios/procedencia y resultados
siguen guardándose por los mecanismos existentes.

`GET /api/bat/resultado/{run_id}` añade `inputs` con el resumen de esa copia; no
envía perfiles ni consulta el catálogo actual. Si falta la columna, la copia es nula
o su versión no está soportada, devuelve `inputs: null`. La web sigue admitiendo
respuestas anteriores sin `inputs`.

### Activación pendiente en una base de pruebas confirmada

1. Aplicar `production/app/sql/20260909_run_input_snapshot.sql` explícitamente con
   `psql -v ON_ERROR_STOP=1 -f ...` sobre la base elegida. No usar sin revisar la
   conexión. La migración es aditiva e idempotente: añade una columna nullable y un
   trigger que impide modificar la copia, incluso de NULL a objeto. No hace backfill.
   Para una instalación nueva, aplicar también esta migración tras el SQL principal
   para instalar la protección de inmutabilidad.
2. Usar la nueva versión de `caso.py`, `run_snapshot.py` y `production/api/bateria.py`.
3. Ejecutar un caso de prueba como **nueva ejecución** mediante el flujo existente.
4. Habilitar su nuevo run_id en `BESS_STUDY_RUN_IDS` y contrastar las tarjetas con
   `inputs`. El ID antiguo debe seguir mostrando los parámetros ausentes.

Para el VPS se han preparado dos scripts que fijan el nombre
`tfm_energia_test`. El primero se detiene si la base ya existe, crea solo las tablas
del módulo, 48 precios sintéticos y perfiles sin información personal. El segundo
ejecuta dos días, compara los parámetros guardados e intenta alterar la copia para
comprobar que PostgreSQL lo rechaza. Ninguno elimina bases ni modifica
`tfm_energia`:

```sh
sudo bash production/app/deploy/prepare_snapshot_test_database.sh /home/ubuntu/scripts
sudo bash production/app/deploy/run_snapshot_test.sh /home/ubuntu/scripts /home/ubuntu/tfm-env/bin/python
```

La prueba requiere que estos cambios estén primero en una copia actualizada del
repositorio del servidor. No debe copiarse el `credentials.json`; utiliza el que ya
existe y solo cambia el nombre de base mediante `TFM_TEST_DB_NAME`, que acepta
exclusivamente nombres terminados en `_test`.

Pruebas locales sin acceso a la base:

```sh
python -m unittest discover -s production/app/tests -p 'test_run_snapshot.py' -v
node --experimental-strip-types --test app/lib/battery-study.test.mjs
```

Incluyen captura independiente de mutaciones, unidades, ausencia frente a cero,
compatibilidad de API y enlace del snapshot al INSERT de la ejecución. La migración
y el guardado real deben verificarse además en PostgreSQL de pruebas antes de activar
en producción. No se ha añadido creación de estudios a la web en este paso.
