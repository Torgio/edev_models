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
