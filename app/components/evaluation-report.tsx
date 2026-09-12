import { Zap } from 'lucide-react';
import { modelColor } from '@/lib/model-color';
import { bestMae, maxCoverageEvaluations, metric, numeric, type Evaluation } from '@/lib/stored-evaluations';

const label = (row?: Evaluation) => row ? `${row.model}${row.seed >= 0 ? ` · semilla ${row.seed}` : ''}` : 'Sin datos';

/** Print and preview share the same report, independent of screen ranking controls. */
export function EvaluationReport({ rows }: { rows: Evaluation[] }) {
  const sorted = [...rows].sort((a, b) => (numeric(a.mae) ? a.mae : Infinity) - (numeric(b.mae) ? b.mae : Infinity) || a.model.localeCompare(b.model));
  const candidates = maxCoverageEvaluations(rows);
  const winner = (key: 'captura_pct' | 'pico_1h_pct') => [...candidates].filter(row => numeric(row[key])).sort((a, b) => b[key]! - a[key]!)[0];
  const low = bestMae(candidates), capture = winner('captura_pct'), peak = winner('pico_1h_pct');
  const context = rows[0];
  const ranges = [...new Set(candidates.map(row => row.model))].map(model => {
    const seeds = new Map(candidates.filter(row => row.model === model && row.seed >= 0 && numeric(row.mae)).map(row => [row.seed, row.mae!]));
    const values = [...seeds.values()];
    return { model, count: seeds.size, low: Math.min(...values), high: Math.max(...values) };
  }).filter(row => row.count >= 2).sort((a, b) => (a.high - a.low) - (b.high - b.low));
  const axisLow = Math.floor(Math.min(...ranges.map(row => row.low)));
  const axisHigh = Math.max(axisLow + 1, Math.ceil(Math.max(...ranges.map(row => row.high))));
  const chunks = Array.from({ length: Math.max(1, Math.ceil(sorted.length / 16)) }, (_, i) => sorted.slice(i * 16, i * 16 + 16));
  const pages = chunks.length + 1;
  const foot = (page: number) => <footer>Pulso Energía · Informe de evaluación<span>Página {page} de {pages}</span></footer>;
  return <>
    {chunks.map((chunk, i) => <article className="evaluation-print-page" key={i}>
      <header className="report-heading"><div className="report-logo"><Zap size={20} /></div><div><small>TFM · Mercado eléctrico · UCM 2026</small><h1>Informe de evaluación de modelos</h1></div><aside>{context?.periodo} · {context?.corte}<br />{new Set(rows.map(row => row.model)).size} modelos · {new Set(rows.filter(row => row.seed >= 0).map(row => row.seed)).size} semillas</aside></header>
      {i === 0 && <div className="evaluation-print-kpis">{[
        ['Menor error', metric(low?.mae, ' €/MWh'), label(low)],
        ['Mayor captura', metric(capture?.captura_pct, ' %'), label(capture)],
        ['Mejor acierto de pico', metric(peak?.pico_1h_pct, ' %'), label(peak)],
      ].map(([title, value, detail]) => <article key={title}><small>{title}</small><strong>{value}</strong><span>{detail}</span></article>)}</div>}
      <h2>Clasificación por error absoluto medio{i > 0 ? ' · continuación' : ''}</h2>
      <table><thead><tr>{['Modelo', 'Semilla', 'Horas', 'MAE', 'Captura', 'Pico ±1 h', 'Mejora'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{chunk.map(row => <tr key={`${row.model}:${row.seed}`}><td><i style={{ background: modelColor(row.model) }} />{row.model}</td><td>{row.seed >= 0 ? row.seed : '—'}</td><td>{row.n_obs?.toLocaleString('es-ES') ?? '—'}</td><td><b>{metric(row.mae)}</b></td><td>{metric(row.captura_pct, ' %')}</td><td>{metric(row.pico_1h_pct, ' %')}</td><td>{metric(row.skill_vs_naive, ' %')}</td></tr>)}</tbody></table>
      <p className="report-note">MAE en €/MWh. Destacados sobre {candidates[0]?.n_obs?.toLocaleString('es-ES') ?? '—'} horas, la cobertura máxima registrada. Las demás coberturas se conservan en la tabla. Igual número de horas no confirma fechas coincidentes.</p>
      {i === 0 && <div className="evaluation-print-callout"><strong>Precisión y captura económica</strong><p>{low && capture ? `${label(low)} registra el menor MAE (${metric(low.mae)} €/MWh); ${label(capture)} registra la mayor captura (${metric(capture.captura_pct)} %), entre las evaluaciones con cobertura máxima.` : 'No hay métricas suficientes para comparar los destacados.'}</p></div>}
      {foot(i + 1)}
    </article>)}
    <article className="evaluation-print-page">
      <h2>Estabilidad entre semillas</h2><p>Rango de MAE entre semillas del mismo modelo, período, configuración y cobertura máxima. Una sola semilla no permite medir estabilidad.</p>
      {ranges.length ? <><p className="report-note">MAE (€/MWh) · escala común {axisLow}–{axisHigh}</p><div className="report-ranges">{ranges.map(row => <div key={row.model}><span>{row.model}</span><div className="report-track"><i style={{ left: `${100 * (row.low - axisLow) / (axisHigh - axisLow)}%`, width: `${Math.max(.5, 100 * (row.high - row.low) / (axisHigh - axisLow))}%`, background: modelColor(row.model) }} /></div><b>{metric(row.high - row.low)}</b></div>)}</div><p className="report-note">La cifra derecha es máximo menos mínimo. {ranges.length} modelos con al menos dos semillas; el resto no se representa.</p></> : <div className="evaluation-print-callout">No hay al menos dos semillas comparables por modelo en esta selección.</div>}
      <h2>Referencias</h2><div className="report-references"><section><h3>Persistencia D−1</h3><p>Referencia que repite el precio del día anterior. La mejora registrada compara el modelo con la referencia empleada por el evaluador.</p></section><section><h3>Oráculo económico</h3><p>Referencia calculada con precio real conocido, bajo los supuestos del evaluador. No equivale a un ingreso garantizado.</p></section></div>
      <h2>Supuestos y límites</h2><ol><li>Período seleccionado: {context?.periodo} · {context?.corte}. Cada semilla mantiene sus resultados.</li><li>Los resultados económicos dependen de los supuestos guardados. {context?.simulador ? 'Esta configuración tiene supuestos registrados.' : 'No hay supuestos registrados; comparabilidad económica no verificada.'}</li><li>RMSE e ingreso diario no están disponibles en la respuesta actual; no se deducen de otras métricas.</li><li>Se incluyen todas las evaluaciones del grupo, con sus coberturas, aunque requieran páginas adicionales.</li></ol>
      {foot(pages)}
    </article>
  </>;
}
