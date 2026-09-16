import { metric } from '@/lib/stored-evaluations';
import { studyCoverage, toKilo, type StudyInputs, type StudyResult } from '@/lib/battery-study';

type Props = { result: StudyResult; inputs: StudyInputs | null; generatedAt: string };

const date = (value: string) => value.split('-').reverse().join('/');
const money = (value: number | null | undefined) => metric(value, ' €');
const origin = (value: string) => value === 'historico' ? 'Histórico' : value === 'simulado' ? 'Proyectado' : value;

/** A report of the saved installation run. It deliberately does not claim daily-model
 * capture or oracle metrics: those belong to the separate operational BESS dataset. */
export function BatteryStudyReport({ result, inputs, generatedAt }: Props) {
  const run = result.run;
  const annual = [...result.anual].sort((a, b) => a.ano - b.ano);
  const projected = annual.filter(row => row.origen === 'simulado');
  const conclusion = run.days_simulated === 0
    ? 'La ejecución es histórica. Permite revisar el despacho, pero no estima un VAN de inversión.'
    : run.npv_p50 == null
      ? 'La simulación se completó, pero no dejó un VAN mediano disponible para resumir la inversión.'
      : run.npv_p50 >= 0
        ? 'En la trayectoria mediana guardada, la inversión presenta un VAN positivo. Revise la dispersión P10–P90 antes de tomar una decisión.'
        : 'En la trayectoria mediana guardada, la inversión presenta un VAN negativo. Conviene revisar tamaño, coste y política antes de avanzar.';

  return <article className="battery-print-report" aria-label="Vista previa del informe del estudio">
    <section className="battery-print-page">
      <header className="battery-report-heading">
        <div className="report-mark">PE</div>
        <div><p>Pulse Energía · Estudio de instalación</p><h1>{inputs?.case.name || 'Informe de batería'}</h1><span>{inputs?.case.code || `Caso ${run.case_id}`} · ejecución {run.run_id}</span></div>
        <div className="report-date"><strong>Informe de simulación</strong><span>Generado {generatedAt}</span></div>
      </header>

      <section className="battery-report-hero">
        <div><p className="report-eyebrow">Decisión de inversión</p><h2>{conclusion}</h2><p>Este documento resume exclusivamente los parámetros y resultados que quedaron guardados en la ejecución seleccionada.</p></div>
        <dl>
          <div><dt>Período del estudio</dt><dd>{inputs ? `${date(inputs.period.date_from)} — ${date(inputs.period.date_to)}` : 'No disponible'}</dd></div>
          <div><dt>Años con resultados</dt><dd>{studyCoverage(annual)}</dd></div>
          <div><dt>Trayectorias de precios</dt><dd>{run.n_scenarios}</dd></div>
        </dl>
      </section>

      <section className="battery-report-kpis" aria-label="Indicadores principales">
        <article><small>VAN mediano</small><strong>{run.days_simulated === 0 ? 'No aplicable' : money(run.npv_p50)}</strong><span>P10 {money(run.npv_p10)} · P90 {money(run.npv_p90)}</span></article>
        <article><small>VAN positivo</small><strong>{run.days_simulated === 0 ? 'No aplicable' : metric(run.npv_positive_pct, ' %')}</strong><span>Proporción de trayectorias evaluadas</span></article>
        <article><small>Ahorro acumulado</small><strong>{money(run.savings_vs_no_batt)}</strong><span>Frente a no instalar batería</span></article>
        <article><small>Uso guardado</small><strong>{metric(run.cycles_per_day)} ciclos/día</strong><span>Vida estimada: {metric(run.life_years, ' años')}</span></article>
      </section>

      <section className="battery-report-section">
        <div className="battery-report-section-heading"><div><p className="report-eyebrow">Recorrido económico</p><h2>Resultados anuales</h2></div><p>Margen medio y rango de trayectorias guardado para cada año. Un año puede estar incompleto.</p></div>
        {annual.length ? <table><thead><tr><th>Año</th><th>Origen</th><th>Días</th><th>Margen medio</th><th>P10</th><th>P50</th><th>P90</th><th>Ciclos/día</th></tr></thead><tbody>
          {annual.map(row => <tr key={row.ano}><td>{row.ano}</td><td>{origin(row.origen)}</td><td>{row.dias}</td><td>{money(row.margen)}</td><td>{money(row.p10)}</td><td>{money(row.p50)}</td><td>{money(row.p90)}</td><td>{metric(row.ciclos_dia)}</td></tr>)}
        </tbody></table> : <p className="battery-report-empty">No se guardaron resultados anuales para esta ejecución.</p>}
      </section>

      <section className="battery-report-callout">
        <strong>{projected.length ? 'Cómo leer la incertidumbre' : 'Alcance del resultado'}</strong>
        <p>{projected.length
          ? 'P10, P50 y P90 describen los resultados entre trayectorias de precios futuras. No son una garantía de rentabilidad ni sustituyen un presupuesto de ingeniería.'
          : 'No hay años proyectados guardados. Por ello, los indicadores de VAN se presentan solo como no aplicables o como valores heredados del cálculo.'}</p>
      </section>

      <footer><span>Pulso Energía · resultado guardado</span><span>1 / 2</span></footer>
    </section>

    <section className="battery-print-page">
      <header className="battery-report-heading compact"><div className="report-mark">PE</div><div><p>Pulse Energía · Estudio de instalación</p><h1>Supuestos y trazabilidad</h1></div><div className="report-date"><span>{inputs?.case.code || `Caso ${run.case_id}`}</span></div></header>
      <section className="battery-report-inputs">
        <div><p className="report-eyebrow">Instalación</p><h2>Datos conservados al ejecutar</h2><dl>
          <div><dt>Consumo</dt><dd>{inputs?.consumption ? `${inputs.consumption.name} · ${metric(inputs.consumption.annual_mwh)} MWh/año` : 'No incluido en la ejecución'}</dd></div>
          <div><dt>Generación</dt><dd>{inputs?.generation ? `${inputs.generation.name} · ${metric(toKilo(inputs.generation.capacity_mwp))} ${inputs.generation.technology === 'fv' ? 'kWp' : 'kW'}` : 'No incluida en la ejecución'}</dd></div>
        </dl></div>
        <div><p className="report-eyebrow">Batería</p><h2>{inputs?.battery.name || 'Parámetros no disponibles'}</h2><dl>
          <div><dt>Potencia</dt><dd>{metric(toKilo(inputs?.battery.power_mw))} kW</dd></div>
          <div><dt>Capacidad</dt><dd>{metric(toKilo(inputs?.battery.capacity_mwh))} kWh</dd></div>
          <div><dt>Duración</dt><dd>{metric(inputs?.battery.duration_h)} h</dd></div>
          <div><dt>Inversión de referencia</dt><dd>{inputs?.battery.capex_eur_mwh == null ? 'No disponible' : `${money(inputs.battery.capex_eur_mwh)} / MWh`}</dd></div>
        </dl></div>
      </section>
      <section className="battery-report-section battery-report-method"><p className="report-eyebrow">Método y límites</p><h2>Qué representa este informe</h2><ul>
        <li>El período conserva {metric(run.days_historical)} días históricos y {metric(run.days_simulated)} días simulados.</li>
        <li>La curva y la instalación se leen de la instantánea guardada; nunca se sustituyen por la configuración actual de la pantalla.</li>
        <li>Los valores por año son resultados medios guardados. La operación horaria y cada trayectoria individual se consultan en la aplicación.</li>
        <li>Este informe no incluye “captura”, coste del error ni comparación contra precio perfecto: esas métricas pertenecen al informe operativo BESS y no fueron calculadas para este estudio de inversión.</li>
      </ul></section>
      {run.notes && <section className="battery-report-callout"><strong>Notas de ejecución</strong><p>{run.notes}</p></section>}
      <section className="battery-report-trace"><p><strong>Ejecutado:</strong> {run.run_at || 'Sin fecha guardada'}</p><p><strong>Curva futura:</strong> {run.curve_generated_at || 'No aplica o no guardada'}</p><p><strong>Huella de matriz:</strong> {run.curve_matrix_hash || 'No guardada'}</p></section>
      <footer><span>Pulso Energía · parámetros registrados</span><span>2 / 2</span></footer>
    </section>
  </article>;
}
