# Registro de cambios — base de datos `tfm_energia`

Documento vivo. Cada vez que se modifica el esquema o los datos de la base de datos
compartida (`91.134.143.153:5432/tfm_energia`), se anota aquí: qué se hizo, por qué,
con qué comando exacto, y cómo se verificó — para que cualquiera del equipo pueda
auditar el porqué de cada tabla sin tener que preguntar ni adivinar.

Este documento **no reemplaza** los scripts de `ingesta/`: si un cambio de esquema
exige tocar un script (por ejemplo, una columna `GENERATED` que ya no admite el
INSERT explícito que hacía un pipeline), aquí se referencia el commit/archivo que
lo resuelve, pero el cambio de código vive en su propio commit.

## Resumen

| # | Fecha | Tabla | Tipo de cambio | Descripción breve | Estado |
|---|---|---|---|---|---|
| 1 | 2026-08-12 | `esios_marketdata` | `DROP COLUMN` | Eliminada `precio_co2_despacho` (100% NULL, huérfana desde jun-2026) | ✅ Hecho |
| 2 | 2026-08-12 | `esios_capacity_available` | `DROP` + `ADD COLUMN GENERATED` | `total_mw` convertida en columna calculada (suma de las 7 tecnologías), backfill automático del histórico | ✅ Hecho |
| 3 | 2026-08-31 | (nuevo rol) `asistente_solo_lectura` | `CREATE ROLE` + `GRANT` | Rol de solo lectura, limitado a 5 tablas, para que el asistente LLM pueda ejecutar SQL generado por el propio modelo sin poder escribir ni ver el resto de la base | ✅ Hecho |

## Detalle

### 1. `esios_marketdata` — eliminar columna `precio_co2_despacho`

- **Fecha:** 2026-08-12
- **Motivo:** columna 100% NULL en las 57.688 filas de la tabla. No aparece referenciada
  en ningún script de `ingesta/` (ni el pipeline diario `esios_daily_marketdata.py` ni
  el histórico `historic_load/esios_marketdata_history.py`), así que eliminarla no
  afecta al cron.
- **Origen del huérfano** (investigado en el historial de git, `git log --all -S"precio_co2_despacho"`):
  - `22-jun-2026`, commit `0eac60a` ("add gitignore to protect credentials and notebooks"):
    las versiones tempranas de los pipelines (`esios_daily.py`, `esios_load.py`, nombres
    previos a los actuales) sí escribían esta columna, mapeada al indicador ESIOS **1391**.
  - `23-jun-2026`, un día después, commit `3537939` ("security: all credential moved"):
    al reestructurar el diccionario de indicadores al formato actual `(id, geo_id)`,
    la entrada `"precio_co2_despacho": 1391` se eliminó del código — probablemente
    porque el indicador 1391 no devolvía datos fiables.
  - Nadie ejecutó el `DROP COLUMN` correspondiente en su momento: la columna quedó
    en la tabla, vacía, desde entonces (~7 semanas).
- **Estado antes:** 32 columnas en `esios_marketdata`; `precio_co2_despacho` con 0/57.688 filas con dato.
- **Comando ejecutado:**
  ```sql
  ALTER TABLE esios_marketdata DROP COLUMN precio_co2_despacho;
  ```
- **Estado después (verificado):** 31 columnas en `esios_marketdata`; columna confirmada
  inexistente vía `information_schema.columns`.
- **Ejecutado por:** Claude Code, con autorización explícita de Willy en el chat.
- **Reversible:** no sin un backup previo — pero al estar 100% vacía, no hay pérdida
  de datos reales.

### 2. `esios_capacity_available` — convertir `total_mw` en columna `GENERATED`

- **Fecha:** 2026-08-12
- **Motivo:** `total_mw` solo tenía dato en 12/2.415 filas (21-jul → 10-ago-2026),
  y en esas 12 coincidía exactamente (diferencia máxima 0.00) con la suma de
  `hydro_mw + pump_mw + nuclear_mw + coal_mw + ccgt_mw + fuel_mw + gas_turbine_mw`.
  El pipeline diario calculaba esa suma a mano; el cargador histórico
  ([historic_load/esios_capacity_available_history.py](../ingesta/historic_load/esios_capacity_available_history.py))
  nunca lo hizo, de ahí el hueco en el histórico 2020→jul-2026.
- **Opciones consideradas:** backfill puntual por `UPDATE`, arreglar y relanzar el
  cargador histórico, o columna `GENERATED`. Se eligió `GENERATED` porque elimina
  la causa de raíz (nadie tiene que recordar recalcularla nunca más) en vez de
  solo corregir el histórico una vez, y porque es el mismo patrón ya validado en
  `entsoe_gen_data`/`entsoe_load_inter`.
- **Estado antes:** 2.415 filas totales, 2.403 con `total_mw` en NULL (99.5%).
- **Comando ejecutado (por Willy, desde pgAdmin4):**
  ```sql
  ALTER TABLE esios_capacity_available DROP COLUMN total_mw;
  ALTER TABLE esios_capacity_available ADD COLUMN total_mw NUMERIC GENERATED ALWAYS AS (
      COALESCE(hydro_mw,0) + COALESCE(pump_mw,0) + COALESCE(nuclear_mw,0) + COALESCE(coal_mw,0)
      + COALESCE(ccgt_mw,0) + COALESCE(fuel_mw,0) + COALESCE(gas_turbine_mw,0)
  ) STORED;
  ```
- **Estado después (verificado):** 2.415/2.415 filas con dato (0 NULL). Confirmado
  vía `information_schema.columns` que `is_generated = 'ALWAYS'` con la expresión
  de suma correcta.
- **Ejecutado por:** Willy, manualmente desde pgAdmin4 (Query Tool).
- **Pendiente relacionado:** editar
  [esios_daily_capacity_available.py:118-119](../ingesta/esios_daily_capacity_available.py#L118-L119)
  para dejar de calcular `total_mw` a mano — una columna `GENERATED` rechaza
  valores explícitos en el INSERT, así que el próximo cron (`5 21 * * *`) fallará
  si esas dos líneas no se quitan antes, y el script actualizado tiene que llegar
  al servidor antes de esa hora.
- **Reversible:** sí, con otro `ALTER TABLE ... DROP COLUMN` + `ADD COLUMN NUMERIC`
  normal (perdería el backfill automático, pero los datos ya calculados se pueden
  volver a insertar con un `UPDATE`).

### 3. Nuevo rol `asistente_solo_lectura` — SQL de solo lectura para el asistente LLM

- **Fecha:** 2026-08-31
- **Motivo:** el asistente del proyecto (`modelos/asistente/`) solo podía responder preguntas
  de datos que encajaran en una de sus funciones Python pre-programadas (ej. "los precios de
  hoy" no se podía responder hasta añadir una función específica ese mismo día). Para
  preguntas que necesitan una consulta ad-hoc que ninguna función cubre, se decidió (con Willy,
  eligiendo entre tres opciones planteadas) darle al modelo de lenguaje una herramienta de SQL
  genérico — pero **nunca** con las credenciales normales del proyecto, que son del usuario
  `postgres` con privilegios totales (superusuario, `rolsuper=true`, confirmado al revisar esto).
  Un SQL escrito al vuelo por un LLM, sin revisión previa de una persona, no debe poder tocar
  nada fuera de un perímetro muy acotado.
- **Tablas con acceso** (las mismas 5 que ya usan las herramientas existentes del asistente,
  ninguna tabla nueva): `spot_price`, `era5_weather_agg`, `esios_capacity_installed`,
  `predictions`, `documentacion_embeddings`.
- **Comando ejecutado:**
  ```sql
  CREATE ROLE asistente_solo_lectura LOGIN PASSWORD '<generada con secrets.token_urlsafe, no en este documento>';
  GRANT CONNECT ON DATABASE tfm_energia TO asistente_solo_lectura;
  GRANT USAGE ON SCHEMA public TO asistente_solo_lectura;
  GRANT SELECT ON spot_price TO asistente_solo_lectura;
  GRANT SELECT ON era5_weather_agg TO asistente_solo_lectura;
  GRANT SELECT ON esios_capacity_installed TO asistente_solo_lectura;
  GRANT SELECT ON predictions TO asistente_solo_lectura;
  GRANT SELECT ON documentacion_embeddings TO asistente_solo_lectura;
  ```
- **Verificado con pruebas reales, no solo confiando en el `GRANT`:**
  - `SELECT` sobre `spot_price`: funciona.
  - `INSERT` sobre `spot_price`: rechazado por Postgres (`InsufficientPrivilege: permission
    denied for table spot_price`).
  - `SELECT` sobre una tabla NO listada (`ttf_m1`, de la ingesta de mercados de gas): rechazado
    (`permission denied for view ttf_m1`) — confirma que el perímetro es real incluso si el
    código Python que valida la consulta tuviera un fallo.
- **Dónde vive la contraseña:** en `credentials.json` de cada persona (clave
  `db_asistente_password`), igual que el resto de credenciales del proyecto — nunca en git.
  Se comparte por fuera del repositorio (Slack/en persona), no aquí.
- **Código que la usa:** `ingesta/config.py::load_config_asistente_solo_lectura()`,
  `modelos/asistente/herramientas.py::consulta_sql_lectura()`.
- **Ejecutado por:** Claude Code, con autorización de Willy tras elegir esta opción entre tres
  alternativas planteadas (mantener herramientas específicas caso por caso / SQL de solo
  lectura acotado a estas 5 tablas / SQL de solo lectura sin restricción de tablas).
- **Reversible:** sí — `DROP ROLE asistente_solo_lectura;` (falla solo si el rol es dueño de
  algún objeto, lo cual no es el caso: solo tiene `GRANT`s recibidos, no objetos propios).

---

*Añadir una fila nueva a la tabla de resumen y una sección de detalle por cada cambio
futuro, con el mismo formato: motivo, comando exacto, estado antes/después verificado,
quién lo ejecutó.*
