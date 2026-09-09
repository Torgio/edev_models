# Pulso Energía — Definición funcional

**Versión:** 2.0

**Actualización:** 3 de septiembre de 2026

**Ámbito:** aplicación web de consulta para el equipo del TFM.

## 1. Objetivo

Pulso Energía convierte los resultados almacenados del proyecto en una lectura operativa con dos prioridades:

1. mostrar la previsión horaria del precio eléctrico y contrastarla con el precio real cuando el día ya está cerrado;
2. mostrar cómo esa previsión se transforma en un plan de carga y descarga de una batería BESS y cuál fue su resultado económico.

La evaluación de modelos y el asistente contextual complementan esas dos funciones. La aplicación es una capa de consulta: no entrena modelos, no ejecuta los pipelines y no recalcula una estrategia BESS alternativa en el navegador.

## 2. Personas usuarias y acceso

El acceso está restringido a cuentas individuales del equipo. Actualmente hay cinco cuentas registradas en el servidor. Los nombres se normalizan en minúsculas y cada persona utiliza su propia contraseña.

- La sesión dura ocho horas.
- La cookie de sesión es `HttpOnly` y `SameSite=Strict`.
- En HTTPS se transmite con el atributo `Secure`.
- Cerrar sesión elimina la cookie de ese navegador.
- Cambiar el archivo de usuarios o una contraseña invalida las sesiones afectadas.
- Todas las cuentas tienen el mismo permiso funcional de lectura; no existen perfiles administrativos en la web.

Las contraseñas, los hashes, las claves del LLM y las credenciales de PostgreSQL no se incluyen en el frontend ni en este documento.

## 3. Navegación principal

Después de iniciar sesión se muestran cuatro secciones:

1. **Predicción** — vista inicial y principal.
2. **Evaluación** — evidencia histórica del rendimiento de los modelos.
3. **BESS** — plan y resultados guardados de la batería.
4. **Asistente** — consultas en lenguaje natural sobre los datos y la metodología del proyecto.

La cabecera identifica al usuario conectado, permite cerrar sesión e indica la última actualización de predicciones cuando existe una fecha válida.

## 4. Predicción

### 4.1 Día inicial y navegación temporal

La aplicación abre el último día cerrado disponible: el último día que contiene la cobertura real esperada y que no corresponde únicamente al horizonte futuro de predicción. Esto permite mostrar conjuntamente qué se predijo y qué ocurrió.

Las flechas permiten avanzar o retroceder por fecha. Los días futuros permanecen accesibles, pero se identifican como precio real pendiente y no muestran errores ni resultados económicos realizados.

La cobertura diaria respeta los días de 23, 24 o 25 horas de `Europe/Madrid`. Las horas de mercado se presentan como **H1, H2, …, H24** —o el número real de períodos del día—, no como horas civiles empezando en 00.

### 4.2 Curva horaria

El gráfico principal muestra:

- hasta tres modelos inicialmente, ampliables desde el selector de series;
- un modelo de referencia destacado;
- el precio real mediante una línea discontinua más fina, cuando está disponible;
- la dispersión central entre los modelos recibidos;
- las horas de carga y descarga del plan BESS guardado, cuando pueden relacionarse con el modelo y la fecha;
- el mínimo y el máximo previstos calculados sobre la serie seleccionada.

La banda de dispersión no es un intervalo predictivo p10/p90 ni un intervalo de confianza. No se etiqueta como tal.

### 4.3 Indicadores diarios

Para el modelo de referencia se muestran:

- precio medio previsto;
- precio medio real;
- diferencia prevista menos real;
- número de horas comunes utilizadas en la comparación;
- cobertura horaria;
- mínimo y máximo previstos y su hora de mercado;
- acierto de la hora de precio máximo dentro de ±1 hora durante la ventana evaluable más reciente.

Las medias y la diferencia utilizan las mismas horas comparables. Un cero o un precio negativo es un valor válido; no se trata como dato ausente.

### 4.4 Detalle auditable

La tabla horaria completa permanece plegada por defecto. Permite revisar cada período, el precio real y las predicciones recibidas sin convertir la pantalla principal en un conjunto de tablas.

## 5. Evaluación

Esta sección demuestra que el menor error no implica necesariamente el mayor valor económico.

### 5.1 Rendimiento en el tiempo

La serie histórica procede de `model_metrics_daily`. Compara diariamente el error del modelo con el naive del precio de la misma hora del día anterior. Los valores por encima de cero indican ventaja frente al naive; los negativos indican que el naive fue mejor.

Se presentan el resumen de los últimos 30 días, los últimos 10 días y los días ganados dentro de la ventana disponible. Si la serie no existe, la sección informa que no está disponible y no fabrica valores de reserva.

### 5.2 Comparación de modelos

Las evaluaciones proceden de `model_metrics` y conservan por separado modelo, semilla, período, corte y configuración del simulador. La pantalla incluye:

- menor MAE;
- mayor captura económica registrada;
- mayor skill frente al naive;
- gráfico de dispersión MAE frente a captura;
- ranking intercambiable por captura, MAE o skill;
- tabla completa con observaciones, pico ±1 hora y cobertura del intervalo del 80 % cuando esas métricas están almacenadas.

El orden del ranking no equivale a una decisión de adopción. Si no existen evaluaciones, no se muestra un leaderboard plausible ni un ganador de reserva.

La captura se describe como comparación con el oráculo definido por el evaluador y sus supuestos. No se presenta como el máximo técnicamente alcanzable por cualquier estrategia de batería.

## 6. BESS

La sección BESS consulta el plan y los resultados persistidos para la fecha y el modelo seleccionados.

Muestra:

- horas y potencia de carga, descarga o espera;
- evolución horaria del estado de carga;
- coste de carga, venta de energía e ingreso neto previsto;
- ingreso realizado cuando ya existe precio real;
- ingreso del oráculo y del naive almacenados;
- captura sobre el oráculo del evaluador;
- ciclos y supuestos registrados: potencia, capacidad, eficiencia, horizonte y regla, cuando estén disponibles.

La aplicación no sustituye un resultado ausente por una simulación calculada en el frontend. Si existe un plan futuro pero todavía no hay precio real, diferencia claramente el ingreso previsto del resultado pendiente. El detalle horario y la definición guardada permanecen plegados por defecto.

## 7. Asistente del proyecto

El asistente permite formular preguntas sobre precios, predicciones, batería, solar, sistema eléctrico y metodología del TFM. Utiliza el endpoint `POST /api/asistente` desarrollado por el equipo y la misma sesión de Pulso Energía; no tiene una contraseña independiente.

Su orden funcional de resolución es:

1. utilizar una herramienta determinista y previamente probada cuando la pregunta encaja;
2. utilizar la búsqueda semántica sobre la documentación del proyecto para preguntas de metodología o decisiones;
3. utilizar SQL dinámico de solo lectura únicamente como último recurso y advertirlo en la respuesta.

Las herramientas cubren, entre otros casos, percentiles y tablas horarias de precio, precios negativos, curva de largo plazo, predicción D+1, simulaciones de batería y autoconsumo, extrapolación de consumo, capacidad instalada y documentación del proyecto.

La respuesta puede incluir Markdown y gráficos generados a partir de resultados de las herramientas. Debe indicar la fuente o herramienta empleada. Las consultas ajenas al ámbito del proyecto se rechazan o redirigen hacia las capacidades disponibles.

La clave de Anthropic denominada `tfm-equipo` reside únicamente en el servidor y nunca se entrega al navegador ni se copia al repositorio.

## 8. Fuentes de datos

| Contenido | Fuente principal |
| --- | --- |
| Predicciones horarias | `predictions` con `source=production` |
| Precio real | `spot_price.es_esios` |
| Evaluaciones por modelo y semilla | `model_metrics` |
| Rendimiento diario frente al naive | `model_metrics_daily` |
| Plan operativo BESS | `bess_plan` |
| Resultado económico BESS | `bess_result` |
| Documentación semántica | `documentacion_embeddings` y documentos indexados |

La web consulta estas fuentes a través de las APIs. No conecta directamente con PostgreSQL y no utiliza cifras de demostración cuando una consulta real viene vacía o falla.

## 9. Estados y comportamiento ante fallos

- **Cargando:** se informa que la consulta está en curso y no se conserva como actual un resultado de otra fecha.
- **Sin datos:** se explica que no hay registros confirmados; se muestran guiones o una sección vacía según corresponda.
- **Sesión caducada:** se vuelve a la pantalla de acceso.
- **API no disponible:** se informa del fallo sin exponer detalles internos ni credenciales.
- **Asistente no disponible:** la previsión, evaluación y BESS continúan funcionando; no se genera una respuesta ficticia.
- **Respuesta futura:** se separan el plan previsto y los indicadores pendientes de precio real.

La página vuelve a consultar los datos al recargar. No existe actualización automática por WebSocket ni polling periódico.

## 10. Arquitectura funcional resumida

El navegador llama únicamente a rutas del mismo origen. La aplicación web reenvía una lista cerrada de solicitudes al VPS mediante HTTPS. El servidor valida la sesión antes de consultar datos o invocar al asistente.

- API de dashboard: `pulso-api`, puerto interno `8000`.
- API del asistente y herramientas del proyecto: servicio interno `8010` detrás de Nginx.
- Base de datos: PostgreSQL `tfm_energia`.
- Zona horaria de visualización: `Europe/Madrid`.

Cuando la web se ejecuta en `http://localhost:3000`, el proxy reemite la cookie del VPS sin el atributo `Secure` exclusivamente para ese origen HTTP local; conserva `HttpOnly`, `SameSite=Strict` y la caducidad. En HTTPS, `Secure` permanece obligatorio.

## 11. Fuera de alcance

La versión actual no incluye:

- entrenamiento o selección automática de modelos;
- edición de predicciones o datos de mercado;
- ejecución manual de pipelines desde la interfaz;
- modificación interactiva de los parámetros del optimizador BESS;
- roles diferenciados, recuperación de contraseña o administración de usuarios desde la web;
- garantía de respuesta del LLM si el proveedor, la clave, el servicio o una fuente requerida no están disponibles.

La publicación de cambios de la interfaz y la actualización de servicios siguen siendo operaciones separadas y controladas.
