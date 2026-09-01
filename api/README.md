# API de Pulso Energía

Documentación de la instalación final y operación:
[Manual de despliegue de Pulso Energía](../docs/manual_despliegue_pulso_energia.md).

## Mejora local: acierto de la hora pico (pendiente de publicar)

`GET /peak-accuracy?model=ensemble&source=production&days=30&end_date=2026-08-30`
devuelve aciertos, días evaluables/excluidos, fechas y detalle por día. Solo lectura,
con la misma protección de sesión que el resto de los datos. No escribe métricas
ni usa `model_metrics`. No cambia el evaluador histórico `pico_1h`.

La ventana es de hasta 30 días naturales en Europe/Madrid, finaliza en `end_date`
o ayer (el más temprano), y excluye días sin precios reales y predicción en todas
las horas esperadas: 23, 24 o 25 según cambio horario. No se extiende la ventana
para completar 30 días evaluables. Datos nulos, no finitos o duplicados excluyen el día.
El primer máximo previsto debe estar a no más de una hora transcurrida de cualquiera
de los máximos reales. No se usa distancia circular entre 23:00 y 00:00 del mismo día.
La consulta utiliza las predicciones de producción actualmente almacenadas; la tabla
no conserva un historial que permita auditar revisiones posteriores. No equivale a
rentabilidad BESS ni al porcentaje histórico del evaluador, cuyas reglas difieren.

## Rutas de la versión publicada

**Distinción:** el apartado siguiente describe la versión que sigue publicada.
La copia local tiene contratos nuevos para `/leaderboard` y `/bess/{target_date}`:

- `/leaderboard`, sin filtros, lee todas las evaluaciones de `model_metrics` y une
  `models` por `(model, seed)` para el estado. Devuelve `origin: model_metrics` y
  `models`, conservando período, corte, observaciones, métricas, simulador y fecha.
  No calcula MAE ni rellena métricas ausentes. El cliente separa grupos y ordena.
- `/bess/{target_date}`, sin filtros, devuelve `plan` y `results` leídos de
  `bess_plan` y `bess_result`. No contiene el simulador diario de la versión publicada.
- Tablas vacías producen listas vacías; errores de acceso producen 503 sin detalles
  de conexión. La autenticación y el carácter de solo lectura se mantienen.
- Cada consulta cierra explícitamente la conexión, además de terminar su transacción.

Pruebas: `python -m unittest api.test_auth api.test_deployment_config api.test_peak_accuracy api.test_stored_results`.

Servicio de solo lectura entre PostgreSQL y el dashboard. Expone cinco rutas:

- `GET /health`
- `GET /days?source=production`
- `GET /predictions/2026-08-30?source=production`
- `GET /leaderboard?source=production&days=30`
- `GET /bess/2026-08-30?source=production&model=gru&duration=2`

En el servidor, las cinco rutas requieren una sesión del equipo. Las rutas
`GET /session`, `POST /login` y `POST /logout` gestionan el acceso, sin consultar
PostgreSQL. Véase [acceso del equipo](deploy/README.md#acceso-del-equipo).

La ruta BESS construye un plan diario de carga y descarga a partir de la predicción del modelo
seleccionado y lo contrasta con el precio real. Por defecto representa una batería de 1 MW,
2 MWh, 90 % de eficiencia y un ciclo diario; `duration` admite 1, 2 o 4 horas.

En desarrollo, si no se activa el modo de entorno, la conexión usa el mismo
`ingesta/credentials.json` que los pipelines. En producción se configuró el rol
dedicado `pulso_dashboard`, con permisos de lectura sobre `predictions` y `spot_price`,
y variables de entorno protegidas; no se copió ese archivo de los pipelines.

Para alojarla sin copiar credenciales de los pipelines, el modo
`DASHBOARD_DB_MODE=environment` usa `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` y
`PGPASSWORD`. Véase [instalación aislada](deploy/README.md). Todas las conexiones
solicitan transacciones de solo lectura y un límite de 15 segundos por consulta.

`DASHBOARD_ALLOWED_ORIGINS` admite una lista separada por comas de orígenes autorizados. El valor
por defecto permite el dashboard publicado y el desarrollo local. El enlace del
sitio es público, pero el acceso a los datos requiere la sesión del equipo.
