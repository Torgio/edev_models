# El asistente del proyecto — resumen para el informe del TFM

Borrador de apoyo, no texto final del informe. Reúne en un solo sitio lo construido durante esta
fase (notas 33-49 de `notas_memoria_tfm.md`), contrastado con el estado real verificado en
GitHub, el servidor y la base de datos — pensado para alimentar las secciones de
**interpretabilidad** y **productivización** que la universidad señaló como las que más hay que
reforzar.

## Qué es, en una frase

Un asistente conversacional (Claude Opus 5, patrón "tool use" / function calling) que responde
preguntas sobre el proyecto usando **exclusivamente** funciones Python deterministas que
consultan la base de datos real del proyecto — el modelo de lenguaje nunca inventa un número,
solo interpreta la pregunta, elige qué función llamar y redacta la respuesta con lo que esa
función devuelve.

## Arquitectura, en tres niveles (por orden de uso)

1. **13 herramientas fijas** — código Python escrito y probado por una persona de antemano
   (percentiles históricos, tabla horaria, tendencia mensual, horas negativas, simulador de
   batería, simulador de autoconsumo solar, curva de precio a 20 años del equipo, extrapolación
   de consumo del cliente, capacidad instalada, la predicción D+1, y una capa de estas 13 más
   dedicada a búsqueda documental).
2. **Búsqueda semántica (RAG)** sobre la documentación del proyecto — 87 fragmentos indexados
   con embeddings locales (sin coste de API adicional), para preguntas de metodología ("por qué
   se hizo X").
3. **SQL de solo lectura, como último recurso** — cuando ninguna de las 13 funciones cubre la
   pregunta, el propio modelo escribe una consulta SELECT, ejecutada contra un rol de Postgres
   dedicado (`asistente_solo_lectura`) que **no puede escribir nada** y solo ve 5 tablas. Cada
   respuesta que sale de aquí se etiqueta explícitamente como "generada dinámicamente", con
   menos garantía que las 13 funciones fijas.

## Estado verificado ahora mismo — no solo "está hecho", contrastado con la realidad

| Dónde | Qué hay | Verificado |
|---|---|---|
| **GitHub** | Todo el código del asistente está fusionado en `main` (rama compartida) | Confirmado leyendo `origin/main` directamente, no asumido |
| **Servidor (VPS)** | Desplegado en `production/api/main.py`, puerto 8010, detrás de nginx, con clave de equipo dedicada de Anthropic | Probado en 3 capas: llamada directa al servicio, llamada pública sin sesión (rechazada correctamente), y llamada con sesión real de Pulso desde el navegador — las tres dieron el resultado esperado |
| **Base de datos** | Tabla nueva `documentacion_embeddings` (87 fragmentos); rol `asistente_solo_lectura` con `GRANT SELECT` sobre 5 tablas, sin ningún otro privilegio | El `INSERT` y el acceso a una tabla no listada se probaron y Postgres los rechazó — el límite es real, no solo de código |
| **Integración con Pulso** | Componente de React (`docs/web/AsistenteWidget.tsx`) listo, con los tokens de diseño reales del sitio | Pendiente de que el front-end de Magui se despliegue en el mismo dominio del VPS (limitación técnica de cookies entre dominios, documentada) |

## Interpretabilidad

Este es el eje que la universidad señaló como el más flojo del grupo. Lo construido aporta en
varios frentes concretos, no solo como discurso:

- **Nunca se disfraza una referencia histórica de predicción.** El sistema distingue, en cada
  respuesta, entre la única predicción real del proyecto (el modelo entrenado, D+1) y cualquier
  otro horizonte, que se presenta siempre como percentiles de lo ya ocurrido o como un escenario
  con supuestos explícitos — nunca como si el modelo lo hubiera "adivinado".
- **Cada número cita su origen.** Toda respuesta indica qué herramienta lo generó, y las
  respuestas que vienen de SQL generado sobre la marcha se marcan como menos fiables que las de
  una función ya probada — es una jerarquía de confianza explícita, no una caja negra.
- **Investigación real de una sospecha de fuga de datos.** Ante la duda de un compañero sobre si
  `es_esios_D`/`pt_entsoe_D` filtraban información del día objetivo, se verificó con datos (no con
  el nombre de la columna): comparando la matriz contra la tabla fuente en varios desfases de
  día, se confirmó que describen el día anterior (información legítima), no el día que se predice
  — 99,85% de coincidencia con el desfase correcto. Es un ejercicio directo de interpretabilidad
  de modelo: usar la importancia de variables para diagnosticar sobreajuste, no solo para explicar
  una predicción.
- **Simulaciones con límites declarados, no ocultos.** El simulador de autoconsumo solar y el de
  batería devuelven explícitamente una lista de `limitaciones` (perfil de consumo plano, sin
  degradación de batería, etc.) que el asistente está obligado a trasladar siempre en la
  respuesta, para que nadie confunda una primera versión con un resultado definitivo.

## Productivización

Aquí el equipo dice que va bien, y el asistente es una pieza concreta de esa historia:

- **Desplegado en producción de verdad**, no en una demo local: servicio systemd, nginx con
  límite de peticiones dedicado (10/min, porque cada pregunta gasta crédito real de la API),
  protegido detrás del login del equipo mediante el mismo mecanismo de sesión que ya usa el
  resto del sitio (`auth_request` reutilizando la cookie de Pulso).
- **Seguridad pensada desde el diseño**, no añadida después: rol de base de datos de solo
  lectura, acotado a 5 tablas, creado específicamente porque las credenciales normales del
  proyecto son de superusuario — decisión documentada y verificada con pruebas adversariales
  (intentos reales de escritura y de acceso a tablas fuera del permiso, ambos rechazados).
- **Gestión de coste real**: clave de API de equipo (no personal), con límite de gasto
  configurado, y un registro local (`historial.jsonl`) de cada pregunta con sus tokens y coste
  estimado — la parte de "esto cuesta dinero de verdad" está gestionada, no ignorada.
- **Iteración basada en fallos reales de producción, no solo en pruebas de laboratorio**: varias
  mejoras (formato de tablas, gráficas proactivas, una herramienta nueva de capacidad instalada,
  un fallo de unidades MW/GW) salieron de encontrar problemas reales al probarlo con el equipo, no
  de una lista de requisitos hecha de antemano — el ciclo de "se prueba, falla, se corrige, se
  verifica de nuevo" queda documentado paso a paso en `notas_memoria_tfm.md`.

## Limitaciones honestas, para que el informe no suene a venta

- La documentación que alimenta la búsqueda semántica (RAG) sigue sin ser un documento revisado y
  aprobado formalmente por todo el equipo — es más objetiva que antes (incluye docstrings de
  código de varios autores, no solo notas de una persona), pero no es "oficial".
- El simulador de batería propio es una versión simplificada; existe un motor del equipo mucho
  más riguroso (programación lineal, degradación, VAN) todavía sin conectar al asistente.
- La predicción D+1 que expone el asistente hereda una limitación conocida del pipeline de datos
  (`DATASET_END` congelado) — se avisa explícitamente en cada respuesta en vez de ocultarlo,
  pero sigue sin estar resuelto de raíz.
- Falta un origen de datos y derechos de utilización documentado explícitamente en algún sitio
  del proyecto (ESIOS/ENTSO-E/AEMET/CDS son fuentes públicas, pero la universidad pide dejarlo
  por escrito, no asumirlo).
