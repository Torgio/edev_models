import '@/app/prediction-report.css';
import { Zap } from 'lucide-react';
import { BESS_DURATION_STUDY, durationErrorCost, euro } from '@/lib/bess-durations';
import type { dailyPrice } from '@/lib/daily-price';
import type { forecastRamp, negativePriceHours } from '@/lib/market-signals';
import type { marketWindows } from '@/lib/market-summary';

export type ReportHour = { label: string; forecast: number | null; actual: number | null; comparison: number | null };

type Props = {
  dayLabel: string;
  model: string;
  comparisonLabel: string | null;
  statusLabel: string;
  generatedAt: string | null;
  coverageLabel: string;
  hours: ReportHour[];
  averages: ReturnType<typeof dailyPrice> | null;
  windows: ReturnType<typeof marketWindows>;
  comparison: { difference: number | null; hours: number };
  ramp: ReturnType<typeof forecastRamp>;
  negatives: ReturnType<typeof negativePriceHours>;
  battery: { income: number | null; oracle: number | null } | null;
};

const PLOT = { width: 688, height: 250, left: 40, right: 686, top: 8, bottom: 212 };

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const num = (value: number, digits = 2) => value.toLocaleString('es-ES', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const price = (value: number | null | undefined, digits = 2) => (finite(value) ? `${num(value, digits)} €/MWh` : '—');
const signed = (value: number | null | undefined, digits = 2) =>
  finite(value) ? `${value > 0 ? '+' : value < 0 ? '−' : ''}${num(Math.abs(value), digits)}` : '—';

/**
 * Informe A4 del día. Maqueta independiente de la pantalla: la vista de predicción
 * es exploratoria y el papel es de consulta, así que aquí manda la tabla horaria y
 * el gráfico solo aporta la forma de la curva.
 *
 * El gráfico es un SVG propio en lugar de Recharts: en impresión el contenedor
 * responsive no tiene ancho fiable, y un viewBox fijo se escala a la caja de la
 * hoja sin depender del layout de pantalla.
 */
export function PredictionReport(props: Props) {
  const { dayLabel, model, comparisonLabel, statusLabel, generatedAt, coverageLabel, hours, averages, windows, comparison, ramp, negatives, battery } = props;

  const forecast = hours.map(hour => hour.forecast);
  const actual = hours.map(hour => hour.actual);
  const paired = hours.filter(hour => finite(hour.forecast) && finite(hour.actual));
  const mae = paired.length ? paired.reduce((total, hour) => total + Math.abs(hour.forecast! - hour.actual!), 0) / paired.length : null;

  const values = hours.flatMap(hour => [hour.forecast, hour.actual, hour.comparison]).filter(finite);
  const low = values.length ? Math.min(0, Math.floor(Math.min(...values) / 20) * 20) : 0;
  const high = values.length ? Math.ceil(Math.max(...values) / 20) * 20 : 100;
  const span = Math.max(1, high - low);
  const x = (index: number) => PLOT.left + (index * (PLOT.right - PLOT.left)) / Math.max(1, hours.length - 1);
  const y = (value: number) => PLOT.bottom - ((value - low) / span) * (PLOT.bottom - PLOT.top);
  const points = (series: (number | null)[]) => series
    .map((value, index) => (finite(value) ? `${x(index).toFixed(1)},${y(value).toFixed(1)}` : null))
    .filter(Boolean).join(' ');

  const valleyIndex = forecast.reduce((best, value, index) => (finite(value) && (best < 0 || value < forecast[best]!) ? index : best), -1);
  const peakIndex = forecast.reduce((best, value, index) => (finite(value) && (best < 0 || value > forecast[best]!) ? index : best), -1);
  const actualPeakIndex = actual.reduce((best, value, index) => (finite(value) && (best < 0 || value > actual[best]!) ? index : best), -1);
  const gap = hours.reduce<{ index: number; value: number } | null>((best, hour, index) => {
    if (!finite(hour.forecast) || !finite(hour.comparison)) return best;
    const difference = hour.forecast - hour.comparison;
    return !best || Math.abs(difference) > Math.abs(best.value) ? { index, value: difference } : best;
  }, null);

  const errorCost = battery && finite(battery.income) && finite(battery.oracle) ? battery.oracle - battery.income : null;
  const ceilingShare = errorCost != null && finite(battery?.oracle) && battery!.oracle! > 0 ? (100 * errorCost) / battery!.oracle! : null;

  const alerts = [
    actualPeakIndex >= 0 && {
      title: 'Máximo real del día', hour: hours[actualPeakIndex].label,
      detail: `${price(actual[actualPeakIndex])} liquidados${peakIndex >= 0 ? `; la previsión situó el máximo en ${hours[peakIndex].label}.` : '.'}`,
    },
    ramp && {
      title: 'Mayor subida prevista', hour: `${hours[ramp.from]?.label ?? '—'} → ${hours[ramp.to]?.label ?? '—'}`,
      detail: `${signed(ramp.increase)} €/MWh en una hora, la rampa más pronunciada de la jornada.`,
    },
    gap && comparisonLabel && {
      title: 'Mayor desvío entre días', hour: hours[gap.index].label,
      detail: `${signed(gap.value)} €/MWh frente al ${comparisonLabel}, donde más se separan las dos jornadas.`,
    },
  ].filter(Boolean) as { title: string; hour: string; detail: string }[];

  const half = Math.ceil(hours.length / 2);
  const blocks = [hours.slice(0, half), hours.slice(half)];
  const grid = Array.from({ length: 5 }, (_, step) => low + (step * span) / 4);

  const foot = (page: number) => <footer>
    <span>Pulso Energía · Informe diario de mercado · {dayLabel}</span><span>Página {page} de 2</span>
  </footer>;

  return <section className="prediction-print-report" aria-label="Informe diario de mercado">
    <article className="prediction-print-page">
      <header className="report-heading">
        <div className="report-logo"><Zap size={20} /></div>
        <div>
          <small>TFM · Mercado eléctrico · UCM 2026</small>
          <h1>Informe diario de mercado</h1>
        </div>
        <aside><strong>{dayLabel}</strong>{statusLabel} · {coverageLabel}<br />Modelo {model || 'sin modelo'}</aside>
      </header>

      <dl className="report-meta">
        <div><dt>Modelo</dt><dd>{model || 'Sin modelo'}</dd></div>
        <div><dt>Comparación</dt><dd>{comparisonLabel ?? 'Sin comparación'}</dd></div>
        <div><dt>Estado de datos</dt><dd>{statusLabel}</dd></div>
        <div><dt>Generado</dt><dd>{generatedAt ?? 'Al exportar'}</dd></div>
      </dl>

      <div className="report-tiles report-tiles-3">
        <article>
          <small>Precio medio previsto</small>
          <strong>{price(averages?.pairedHours ? averages.pairedPrediction : averages?.predicted)}</strong>
          <span>{model || 'sin modelo'} · {averages?.predictedHours ?? 0}/{averages?.expectedHours ?? 24} horas</span>
        </article>
        <article>
          <small>Precio medio real</small>
          <strong>{price(averages?.pairedReal)}</strong>
          <span>{averages?.pairedHours ?? 0}/{averages?.expectedHours ?? 24} horas comparables</span>
        </article>
        <article>
          <small>Desvío previsto − real</small>
          <strong>{signed(averages?.difference)} €/MWh</strong>
          <span>MAE {mae == null ? '—' : num(mae)} · {statusLabel.toLowerCase()}</span>
        </article>
      </div>

      <h2>Curva del día</h2>
      <p className="report-lead">Previsión de {model || 'el modelo de referencia'} frente al precio real liquidado{comparisonLabel ? ` y al ${comparisonLabel}` : ''}. Escala común en €/MWh.</p>

      <svg className="report-chart" viewBox={`0 0 ${PLOT.width} ${PLOT.height}`} role="img" aria-label="Curva horaria de precios">
        {grid.map(value => <g key={value}>
          <line x1={PLOT.left} x2={PLOT.right} y1={y(value)} y2={y(value)} stroke="#e2e9e5" strokeWidth={1} />
          <text x={PLOT.left - 6} y={y(value) + 3} textAnchor="end" fontSize={8} fill="#7d9a91">{num(value, 0)}</text>
        </g>)}
        {ramp && <rect x={x(ramp.from)} y={PLOT.top} width={Math.max(2, x(ramp.to) - x(ramp.from))} height={PLOT.bottom - PLOT.top} fill="#a8842c" fillOpacity={.1} />}
        {comparisonLabel && <polyline points={points(hours.map(hour => hour.comparison))} fill="none" stroke="#5f736c" strokeWidth={1.6} strokeDasharray="7 5" />}
        <polyline points={points(actual)} fill="none" stroke="#142e28" strokeWidth={2} strokeDasharray="4 4" />
        <polyline points={points(forecast)} fill="none" stroke="#4a9b57" strokeWidth={2.6} />
        {valleyIndex >= 0 && <>
          <circle cx={x(valleyIndex)} cy={y(forecast[valleyIndex]!)} r={4.5} fill="#e58b45" stroke="#142e28" strokeWidth={1.4} />
          <text x={x(valleyIndex) + 8} y={y(forecast[valleyIndex]!) + 4} fontSize={8.5} fontWeight={600} fill="#142e28">Valle previsto</text>
        </>}
        {hours.map((hour, index) => (index % 3 === 0 || index === hours.length - 1
          ? <text key={hour.label} x={x(index)} y={238} textAnchor="middle" fontSize={8} fill="#7d9a91">{hour.label}</text>
          : null))}
      </svg>

      <div className="report-legend">
        <span><i style={{ borderTop: '2.6px solid #4a9b57' }} />Previsión {model || 'de referencia'}</span>
        <span><i style={{ borderTop: '2px dashed #142e28' }} />Precio real {dayLabel}</span>
        {comparisonLabel && <span><i style={{ borderTop: '2px dashed #5f736c' }} />Precio real {comparisonLabel}</span>}
        {ramp && <span><i style={{ height: 9, background: '#a8842c', opacity: .28 }} />Mayor rampa prevista</span>}
      </div>

      <h2>Detalle horario</h2>
      <p className="report-lead">Las cifras que el gráfico resume. Desvío es previsión menos precio real; el signo indica si el modelo quedó por encima o por debajo.</p>
      <div className="report-hours">
        {blocks.map((block, index) => <table key={index}>
          <thead><tr><th>Hora</th><th>Prev.</th><th>Real</th><th>Desvío</th></tr></thead>
          <tbody>{block.map(hour => <tr key={hour.label}>
            <td>{hour.label}</td>
            <td>{finite(hour.forecast) ? num(hour.forecast, 1) : '—'}</td>
            <td>{finite(hour.actual) ? num(hour.actual, 1) : '—'}</td>
            <td className="report-bias">{finite(hour.forecast) && finite(hour.actual) ? signed(hour.forecast - hour.actual, 1) : '—'}</td>
          </tr>)}</tbody>
        </table>)}
      </div>
      {foot(1)}
    </article>

    <article className="prediction-print-page">
      <h2 style={{ marginTop: 0 }}>Lectura del día</h2>
      <p className="report-lead">Valle, rampa y horas bajo cero se calculan para este día y el modelo de referencia. Describen la curva; no atribuyen su causa.</p>
      <div className="report-tiles report-tiles-4">
        <article>
          <small>Valle previsto</small>
          <strong>{price(valleyIndex >= 0 ? forecast[valleyIndex] : null)}</strong>
          <span>{valleyIndex >= 0 ? hours[valleyIndex].label : 'Sin datos'}</span>
        </article>
        <article>
          <small>Máximo previsto</small>
          <strong>{price(peakIndex >= 0 ? forecast[peakIndex] : null)}</strong>
          <span>{peakIndex >= 0 ? hours[peakIndex].label : 'Sin datos'}</span>
        </article>
        <article>
          <small>Mayor rampa</small>
          <strong>{ramp ? `${signed(ramp.increase)} €/MWh` : '—'}</strong>
          <span>{ramp ? `${hours[ramp.from]?.label ?? '—'} → ${hours[ramp.to]?.label ?? '—'}` : 'Sin rampa registrada'}</span>
        </article>
        <article>
          <small>Horas negativas</small>
          <strong>{negatives.entries.length}</strong>
          <span>Previstas {negatives.predicted} · reales {negatives.actual}</span>
        </article>
      </div>
      <p className="report-note">Tramos de 3 horas: mínimo {windows.cheapest?.label ?? '—'} · máximo {windows.priciest?.label ?? '—'}.{comparison.hours ? ` Frente al día comparado, ${signed(comparison.difference)} €/MWh sobre ${comparison.hours} horas comparables.` : ''}</p>

      <h2>Alertas del día</h2>
      <p className="report-lead">Lectura automática de la curva, ordenada por el interés operativo del turno.</p>
      <div className="report-alerts">
        {alerts.length ? alerts.map(alert => <div key={alert.title}>
          <strong>{alert.title}</strong><em>{alert.hour}</em><span>{alert.detail}</span>
        </div>) : <div><strong>Sin alertas</strong><em>—</em><span>La jornada no presenta máximos, rampas ni desvíos destacables con los datos disponibles.</span></div>}
      </div>

      {battery && <>
        <h2>Impacto económico</h2>
        <p className="report-lead">Mismo plan de batería: ingreso liquidado con la previsión frente al techo calculado con el precio real ya conocido.</p>
        <div className="report-tiles report-tiles-3">
          <article><small>Ingreso con previsión</small><strong>{euro(battery.income)}</strong><span>{model || 'sin modelo'} · {dayLabel}</span></article>
          <article><small>Techo con precio real</small><strong>{euro(battery.oracle)}</strong><span>Decisión con información perfecta</span></article>
          <article><small>Coste del error</small><strong>{errorCost == null ? '—' : euro(errorCost)}</strong><span>{ceilingShare == null ? 'Sin liquidación completa' : `${num(ceilingShare, 1)} % del techo`}</span></article>
        </div>

        <p className="report-kicker" style={{ marginTop: 18 }}>Estudio anual · 1 MW</p>
        <table>
          <thead><tr><th>Duración</th><th>Ingreso modelo</th><th>Techo oráculo</th><th>Coste del error</th><th>Captura</th></tr></thead>
          <tbody>{BESS_DURATION_STUDY.map(row => <tr key={row.hours}>
            <td>{row.hours} h</td>
            <td>{euro(row.model)}</td>
            <td>{euro(row.oracle)}</td>
            <td>{euro(durationErrorCost(row))}</td>
            <td>{num(row.capture, 1)} %</td>
          </tr>)}</tbody>
        </table>
        <p className="report-note">Año completo con 1 MW de potencia, no la jornada de este informe. Arbitraje bruto en mercado diario: sin degradación, peajes, O&amp;M, intradiario, balance ni capacidad. Más duración captura más del techo, pero el coste absoluto del error también crece.</p>
      </>}

      <h2>Metodología y límites</h2>
      <ol>
        <li>Precios del mercado diario español. Datos almacenados en UTC y presentados en Europe/Madrid; un día natural puede tener 23, 24 o 25 horas y solo entran las horas del día seleccionado.</li>
        <li>La previsión es la del modelo indicado en la cabecera. El desvío se calcula solo sobre las horas con previsión y precio real; el número de horas comparables consta en cada cifra.</li>
        <li>La banda de dispersión que aparece en pantalla es el recorrido central entre modelos, no un intervalo de confianza: no expresa probabilidad.</li>
        <li>El ingreso y el techo del plan de batería salen del simulador con los supuestos guardados. El techo usa el precio real ya conocido, por lo que no es un ingreso alcanzable en tiempo real.</li>
        <li>El estudio anual por duración procede de una simulación independiente a 1 MW y no se deduce de esta jornada.</li>
        <li>Valle, rampa y horas bajo cero describen la forma de la curva prevista. No identifican la causa del precio ni sustituyen el análisis de fundamentales.</li>
      </ol>
      {foot(2)}
    </article>
  </section>;
}