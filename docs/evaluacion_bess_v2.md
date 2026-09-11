# Evaluación BESS y coherencia temporal — v2

Implementación local del 10 de septiembre de 2026. No implica despliegue ni recálculo de la base publicada.

## Contrato físico

`modelos/bess_evaluation.py` es el motor compartido por el evaluador histórico, la ventana diaria de evaluación y el plan D+1 de `scripts/run_diario.py`.

- Potencia de carga y descarga: 1 MW en el lado AC; capacidad interna: 2 MWh.
- Rendimiento de ida y vuelta: 90 %. Cada dirección aplica `sqrt(0.9)`.
- Balance: `SOC[t] = SOC[t-1] + (eta*carga[t] - descarga[t]/eta)*duracion`.
- SOC inicial y final: 0 MWh; límites: 0–2 MWh.
- Carga y descarga excluyentes mediante una variable binaria por período, incluso con precios negativos.
- Hasta un ciclo equivalente diario: MWh internos descargados / capacidad. Operar es opcional; el límite no obliga a completar un ciclo.
- Ingreso horario: `(descarga-carga)*precio*duracion`. Las potencias ya son AC: no aplicar otra vez la eficiencia al ingreso.
- Coste configurable por MWh descargado, inicialmente 0 €/MWh. Por tanto, la configuración del dashboard sigue midiendo arbitraje bruto, sin peajes, degradación, O&M ni CAPEX.
- El solver debe alcanzar el óptimo con tolerancia relativa de 1e-8 en 30 segundos. Un fallo o límite de tiempo aborta el cálculo; no se guarda un plan de reserva.

La formulación adopta el balance físico del optimizador de estudios de inversión, pero este módulo D+1 tiene su propio horizonte y límite de ciclos. `production/app/optimiza_bateria.py` y los estudios de inversión no se modifican en esta entrega.

## Plan guardado frente a simulación

`bess_plan` guarda las potencias y el SOC decididos con la previsión. `bess_result` valora ese mismo despacho con el precio real publicado; no vuelve a decidir usando una predicción que pueda haber cambiado. El oráculo y el naive usan idéntico contrato físico y el mismo día objetivo. El oráculo es el óptimo bajo estos supuestos, no una previsión disponible para operar.

Un plan ausente, incompleto, físicamente inválido o de la regla antigua no produce un resultado v2. Se registra el motivo en consola. Los planes antiguos no se reconstruyen y sus resultados existentes no se reclasifican.

`model_metrics` sí simula cada modelo para poder compararlos aunque solo se guarde el plan del campeón. No representa operación ejecutada de todos los modelos. Ninguno de estos importes certifica actividad física de una batería: son valoraciones de despacho con precios publicados.

## Contrato temporal

Los instantes se conservan con zona; la fecha operativa se calcula en `Europe/Madrid`. La cobertura se contrasta con el calendario completo de ese día: 23, 24 o 25 horas. Ni una hora ausente ni un instante duplicado pasan por día completo.

La persistencia usa la misma hora civil del día anterior, no un desplazamiento fijo de 24 horas UTC. Si ayer tuvo dos 02:00, usa la primera; si esa hora no existió, el baseline queda ausente. Las dos horas reales de octubre se conservan. La serie diaria marca el cambio de hora y el día posterior.

Los CSV antiguos de 24 columnas no contienen dos ocurrencias de la hora de octubre. Una hora ambigua no se inventa ni duplica: se excluye y ese día no obtiene evaluación económica. Para medir ese día hace falta la fuente con instantes UTC.

La planificación comprueba el corte operativo actual del pipeline: las 12:00 de Madrid del día anterior al objetivo, antes y después de resolver. No permite rehacer planes históricos como si fueran decisiones previas. Las lecturas de predicciones y planes exigen `updated_at` anterior al corte. Es una protección conservadora: una fila sobrescrita después se excluye aunque existiera una versión válida anterior. No reemplaza un archivo inmutable de emisiones, ni prueba por sí sola que todas las variables estuvieran disponibles en origen.

La resolución de entrada del pipeline continúa siendo horaria. El motor admite otra duración y tiene una prueba a 15 minutos, pero esta entrega no migra ingesta, redes, base de datos ni interfaz al mercado cuarto-horario.

## Comparabilidad y versiones

El ranking histórico y la ventana de producción usan la intersección exacta de instantes finitos entre sus modelos y referencias. Un modelo con menor cobertura puede reducir la muestra de todos: se informa del número común y solo se liquida un día si permanece completo. La serie diaria mantiene su evaluación individual con MAEs pareados.

Los supuestos contienen `version=bess_fisico_v2`. Las evaluaciones añaden el rango UTC, número de períodos y SHA-256 de los instantes de la muestra. Así la agrupación existente de la aplicación no mezcla ventanas distintas por tener simplemente el mismo número de observaciones.

Los nuevos CSV del leaderboard incluyen el JSON `simulador`. `registrar_modelos.py` conserva esos metadatos; un CSV antiguo sin ellos mantiene su definición histórica.

## Validación y activación

Pruebas sin PostgreSQL:

```sh
.venv/bin/python -m unittest modelos.test_bess_evaluation scripts.test_serie_diaria api.test_peak_accuracy api.test_stored_results -q
```

Antes de activar en el servidor, distribuir juntos los módulos nuevos `bess_evaluation.py` y `tiempo_mercado.py`, los evaluadores y el planificador. Se necesita SciPy con `scipy.optimize.milp` (SciPy >=1.9). No se requiere migración de tablas.

Después, con el entorno autorizado de datos, ejecutar `scripts/evaluar_diario.py --simulacro` para revisar cobertura y resultados sin escribir. Los resultados históricos físicos se recalculan mediante el evaluador de modelos y se identifican como simulación; los planes operativos v2 empezarán a existir desde la activación. No usar la nueva regla para renombrar ingresos antiguos sin recalcularlos.

## Validación con PostgreSQL real — 10 de septiembre de 2026

Completada desde el entorno local contra la base configurada, con `transaction_read_only=on`, aislamiento `REPEATABLE READ` y cierre con `rollback`. No se desplegó código ni se escribieron resultados. SSH con la autenticación local disponible fue rechazado.

- Ventana inspeccionada: 13 de agosto–11 de septiembre de 2026. El último día es futuro respecto a la validación, pero ya dispone de precio publicado; sus importes son simulaciones, no ingresos ejecutados.
- Predicciones anteriores al corte y con precio: 2.904 filas, correspondientes a 121 pares modelo/día. Se resolvieron y validaron físicamente los 121 despachos simulados.
- Hay 5.424 filas etiquetadas `production` cuya última escritura no acredita disponibilidad anterior al corte. Se excluyeron de la evaluación operativa; esta evidencia no prueba cuándo se generó una versión anterior.
- La intersección de todos los modelos queda en 24 horas porque `ensemble` solo aporta un día. Los otros 12 modelos comparten 240 horas (2–11 de septiembre). El análisis separado de esta cohorte se ejecutó en memoria; aún no existe selección de cohortes en el evaluador diario.
- En esas 240 horas, `boosting` obtiene MAE 19,27 €/MWh y skill 23,78 %, pero su arbitraje simulado es 371,77 €/día frente a 374,21 €/día del naive. `ensemble11` obtiene 375,98 €/día. Son resultados bajo 1 MW / 2 MWh y costes de descarga cero; diez días no bastan para una decisión de adopción.
- Los diez planes diarios guardados en el intervalo siguen usando la regla antigua. Ocho tienen última escritura posterior al corte; solo los del 2 y 11 de septiembre pasan la comprobación temporal, y tampoco se liquidan como v2 por ser legado.
- Se confirmó un fallo físico en datos reales: el plan `ensemble11` del 8 de septiembre alcanza SOC −1 MWh. Los nuevos despachos simulados respetaron 0–2 MWh.

En aquella comprobación quedaron pendientes la selección de cohorte, la activación en el servidor y la verificación del primer plan v2. La selección está resuelta en la actualización descrita a continuación; la activación y el primer ciclo siguen pendientes. Los planes antiguos deben conservar su identificación histórica y no presentarse como operaciones físicas v2.

## Selección automática e instalación preparada

El evaluador diario ya usa por defecto `--cohorte continua`: al menos 90 % de la cobertura
máxima disponible y presencia en el último día. La selección depende únicamente de la
disponibilidad, nunca del MAE ni del ingreso. `--cohorte todos` conserva la comparación
explícita con todos los modelos. Los motivos de exclusión viajan con los supuestos.
La intersección temporal exacta se calcula después de seleccionar la cohorte.

Al publicar una ventana, las filas ausentes de `prod_30d/global` quedan con métricas
nulas y cero observaciones en la misma transacción que escribe la cohorte actual.
Así no se conserva un ganador retirado de una ventana anterior. No se modifican las
ventanas de test/validación ni los resultados BESS históricos.

`scripts/verificar_bess_v2.py` verifica, en solo lectura, el último plan v2 y su
liquidación: cobertura, corte de escritura, SOC y consistencia de cada indicador con
el despacho guardado. Sale con código 3 si aún falta plan, precio o resultado;
con 0 si está verificado; una inconsistencia produce un error.

El paquete se prepara con `production/deploy/build_bess_v2.py`. El instalador realiza
un simulacro antes de copiar, verifica SHA-256, guarda los archivos sustituidos,
comprueba otra vez y restaura el código anterior si falla. No cambia cron ni servicios.
La primera planificación v2 depende de que el cron existente de 11:35 esté instalado;
la liquidación depende del evaluador posterior y de la publicación del precio real.

La selección automática se volvió a validar contra PostgreSQL real: excluye `ensemble`
por disponibilidad y obtiene 240 horas comunes para los 12 modelos continuos. La
validación fue de solo lectura. La guía de activación está en
[Instalación BESS](../production/deploy/BESS_INSTALL.md).

El paquete final `pulso-bess-v2-update.tar.gz` se extrajo en un directorio aislado y
su instalador pasó la comprobación contra PostgreSQL real sin `--apply`. La suite
ampliada pasó 65 pruebas, incluidos selección de cohorte, verificador de liquidación
y restauración del instalador ante un fallo simulado. Aún no se ha activado en el VPS.

## Activación confirmada por salida del servidor

El 10 de septiembre de 2026 el usuario ejecutó el instalador con `--apply`. La
salida compartida confirma que pasaron el simulacro previo y el posterior desde
`/home/ubuntu/scripts/scripts/evaluar_diario.py`, con 240 horas comunes. El instalador
terminó con `BESS v2 activado` y guardó la copia en
`/home/ubuntu/scripts/.bess-backups/20260910-194352-441233`.

La instalación no escribió métricas ni modificó cron. Pendiente de comprobar:
la ejecución normal que persista el nuevo ranking, las entradas activas de cron
y el primer plan v2 con su liquidación. Los avisos de pandas sobre conexiones DBAPI
no impidieron las lecturas ni la validación mostrada.
