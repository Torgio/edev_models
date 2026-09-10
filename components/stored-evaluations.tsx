'use client';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Cell, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { bestMae, evaluationGroup, maxCoverageEvaluations, metric, numeric, rankedEvaluations, type Evaluation, type Order } from '@/lib/stored-evaluations';
import { PerformanceHistory } from '@/components/performance-history';
import { modelColor } from '@/lib/model-color';
import { formatEnergyPrice } from '@/lib/price-format';

const orderLabels: Record<Order, string> = {
  captura_pct: 'Captura (%)',
  mae: 'Error (€/MWh)',
  skill_vs_naive: 'Mejora (%)',
};
const seed = (value: number) => value === -1 ? 'No aplica' : String(value);
const bestBy = (rows: Evaluation[], key: 'captura_pct' | 'skill_vs_naive') =>
  rows.filter(row => numeric(row[key])).sort((a, b) => b[key]! - a[key]!)[0];
const coverage = (row: Evaluation | undefined) => row && numeric(row.n_obs)
  ? `${row.n_obs.toLocaleString('es-ES')} horas evaluadas`
  : 'Cobertura no disponible';

function EvaluationTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: Evaluation }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return <div className="evaluation-tooltip">
    <strong>{row.model}</strong><span>Semilla {seed(row.seed)}</span>
    <dl><div><dt>MAE</dt><dd>{formatEnergyPrice(row.mae)}</dd></div>
      <div><dt>Captura</dt><dd>{metric(row.captura_pct, ' %')}</dd></div>
      <div><dt>Skill</dt><dd>{metric(row.skill_vs_naive, ' %')}</dd></div></dl>
  </div>;
}

export function StoredEvaluations({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [retry, setRetry] = useState(0);
  const [rows, setRows] = useState<Evaluation[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [group, setGroup] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [maximumOnly, setMaximumOnly] = useState(true);
  const [order, setOrder] = useState<Order>('captura_pct');

  useEffect(() => {
    setStatus('loading');
    const controller = new AbortController();
    fetch('/api/dashboard/leaderboard', { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (response.status === 401) onSessionExpired();
      if (!response.ok) throw new Error();
      const result = await response.json() as { origin: string; models: Evaluation[] };
      if (result.origin !== 'model_metrics' || !Array.isArray(result.models)) throw new Error();
      if (!controller.signal.aborted) { setRows(result.models); setStatus('ready'); }
    }).catch(() => { if (!controller.signal.aborted) { setRows([]); setStatus('error'); } });
    return () => controller.abort();
  }, [retry]);

  const groups = [...new Map(rows.map(row => [evaluationGroup(row), row])).entries()];
  const selected = groups.some(([key]) => key === group) ? group : groups[0]?.[0] ?? '';
  const ranked = rankedEvaluations(rows, selected, order);
  const comparable = maxCoverageEvaluations(ranked);
  const rankingRows = maximumOnly ? comparable : ranked;
  const identity = (row: Evaluation) => `${row.model}:${row.seed}`;
  const chosen = ranked.filter(row => selection.includes(identity(row)));
  const scatter = rankingRows.filter(row => numeric(row.mae) && numeric(row.captura_pct));
  const context = ranked[0];
  const lowMae = bestMae(comparable);
  const highCapture = bestBy(comparable, 'captura_pct');
  const highSkill = bestBy(comparable, 'skill_vs_naive');

  return <><section className="evaluation-section" id="hitos" aria-labelledby="evaluation-title">
    <div className="evaluation-header">
      <div><p className="section-label">Comparación por período</p><h2 id="evaluation-title">Evaluación de modelos</h2>
        <p>Compara precisión y captura económica bajo la misma configuración. Cada semilla conserva sus resultados.</p></div>
      {groups.length > 0 && <label>Período y configuración
        <NativeSelect value={selected} onChange={event => { setGroup(event.target.value); setSelection([]); }}>
          {groups.map(([key, row], index) => <NativeSelectOption key={key} value={key}>{row.periodo} · {row.corte} · configuración {index + 1}</NativeSelectOption>)}
        </NativeSelect>
      </label>}
    </div>

    {status !== 'ready' ? <div className="evaluation-empty" role="status">{status === 'loading' ? 'Consultando evaluaciones…' : 'No se pudieron consultar las evaluaciones.'}{status === 'error' && <Button variant="outline" onClick={() => setRetry(value => value + 1)}>Reintentar</Button>}</div>
      : !rows.length ? <div className="evaluation-empty" role="status">No hay evaluaciones guardadas.</div> : <>
      <div className="evaluation-kpis">
        <article><span>Menor error · cobertura máxima</span><strong>{lowMae?.model ?? 'Sin datos'}</strong><b>{formatEnergyPrice(lowMae?.mae)}</b><small>{lowMae ? `${lowMae.model} · semilla ${seed(lowMae.seed)} · ${coverage(lowMae)}` : 'Sin valor comparable'}</small></article>
        <article><span>Mayor captura · cobertura máxima</span><strong>{highCapture?.model ?? 'Sin datos'}</strong><b>{metric(highCapture?.captura_pct, ' %')}</b><small>{highCapture ? `${highCapture.model} · semilla ${seed(highCapture.seed)} · ${coverage(highCapture)}` : 'Sin valor comparable'}</small></article>
        <article><span>Mayor mejora frente al modelo simple</span><strong>{highSkill?.model ?? 'Sin datos'}</strong><b>{metric(highSkill?.skill_vs_naive, ' %')}</b><small>{highSkill ? `${highSkill.model} · semilla ${seed(highSkill.seed)} · ${coverage(highSkill)}` : 'Sin valor comparable'}</small></article>
      </div>

      <p className="evaluation-coverage-note">Los destacados usan la mayor cobertura registrada del grupo ({comparable[0]?.n_obs?.toLocaleString('es-ES') ?? '—'} horas). Igual número de horas no confirma que sean las mismas fechas. {comparable.length < 2 ? 'No hay al menos dos evaluaciones con esa cobertura para establecer una comparación.' : 'En caso de empate se muestra una de las evaluaciones.'} {!context?.simulador && 'No hay supuestos registrados: la comparabilidad económica no está verificada.'}</p>
      <label className="evaluation-coverage-toggle"><Checkbox checked={maximumOnly} onCheckedChange={setMaximumOnly} />Mostrar solo cobertura máxima en el gráfico y la clasificación</label>
      <div className="evaluation-grid">
        <article className="scatter-card">
          <div className="visual-heading"><div><p className="section-label">Una marca por modelo y semilla</p><h3>MAE frente a captura económica</h3></div><span>{scatter.length} evaluaciones con ambas métricas</span></div>
          <div className="scatter-wrap">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={320}>
              <ScatterChart margin={{ top: 22, right: 24, bottom: 30, left: 12 }}>
                <CartesianGrid stroke="#e1e8e4" strokeDasharray="3 4" />
                <XAxis type="number" tickFormatter={value => metric(value)} dataKey="mae" name="MAE" domain={['dataMin - 1', 'dataMax + 1']} tick={{ fontSize: 11, fill: '#667770' }} tickLine={false}
                  label={{ value: 'MAE (€/MWh) · menor es mejor', position: 'bottom', offset: 12, fontSize: 11, fill: '#52685e' }} />
                <YAxis type="number" tickFormatter={value => metric(value)} dataKey="captura_pct" name="Captura" domain={['dataMin - 2', 'dataMax + 2']} width={58} tick={{ fontSize: 11, fill: '#667770' }} tickLine={false}
                  label={{ value: 'Captura registrada (%)', angle: -90, position: 'insideLeft', fontSize: 11, fill: '#52685e' }} />
                <Tooltip content={<EvaluationTooltip />} cursor={{ strokeDasharray: '3 4' }} />
                <Scatter data={scatter} isAnimationActive={false}>{scatter.map(row => <Cell key={`${row.model}:${row.seed}`} fill={modelColor(row.model)} />)}</Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p>La captura compara el ingreso del modelo con el oráculo definido por el evaluador y sus supuestos registrados. No representa necesariamente el máximo operable de una batería.</p>
        </article>

        <aside className="ranking-card">
          <div className="visual-heading"><div><p className="section-label">Orden según el criterio elegido</p><h3>Clasificación</h3></div></div>
          <div className="ranking-tabs" role="group" aria-label="Orden del ranking">
            {(Object.keys(orderLabels) as Order[]).map(key => <button type="button" key={key} aria-pressed={order === key}
              className={order === key ? 'active' : ''} onClick={() => setOrder(key)}>{orderLabels[key]}</button>)}
          </div>
          <ol className="visual-ranking">
            {rankingRows.slice(0, 5).map((row, index) => <li key={`${row.model}:${row.seed}`}>
              <span className="rank-number">{index + 1}</span><i style={{ background: modelColor(row.model) }} />
              <span><strong>{row.model}</strong><small>Semilla {seed(row.seed)} · {coverage(row)}</small></span>
              <b>{order === 'mae' ? formatEnergyPrice(row.mae) : order === 'captura_pct' ? metric(row.captura_pct, ' %') : metric(row.skill_vs_naive, ' %')}</b>
            </li>)}
          </ol>
          {!rankingRows.length && <p>No hay evaluaciones con cobertura disponible.</p>}
          <p>El orden cambia con la métrica. No implica que el primer modelo esté adoptado.</p>
        </aside>
      </div>

      <section className="evaluation-compare" aria-labelledby="evaluation-compare-title">
        <h3 id="evaluation-compare-title">Comparar evaluaciones</h3><p>Elige hasta tres modelos y semillas. Las métricas pertenecen al período seleccionado.</p>
        <div className="evaluation-choices">{ranked.map(row => <label key={identity(row)}><Checkbox checked={selection.includes(identity(row))} disabled={chosen.length >= 3 && !selection.includes(identity(row))} onCheckedChange={checked => setSelection(current => checked ? [...current, identity(row)].slice(0, 3) : current.filter(key => key !== identity(row)))} />{row.model} · semilla {seed(row.seed)}</label>)}</div>
        {chosen.length > 0 && <div className="evaluation-comparison-grid" aria-live="polite">{chosen.map(row => <article key={identity(row)}><h4>{row.model}</h4><p>Semilla {seed(row.seed)} · {row.estado ?? 'Sin estado'}</p><dl>
          <div><dt>Error medio absoluto ↓</dt><dd>{formatEnergyPrice(row.mae)}</dd></div><div><dt>Captura económica ↑</dt><dd>{metric(row.captura_pct, ' %')}</dd></div><div><dt>Mejora frente al modelo simple ↑</dt><dd>{metric(row.skill_vs_naive, ' %')}</dd></div><div><dt>Cobertura</dt><dd>{coverage(row)}</dd></div>
        </dl></article>)}</div>}
        {chosen.length > 1 && <p className="evaluation-coverage-note">{chosen.some(row => !numeric(row.n_obs) || row.n_obs !== chosen[0].n_obs) ? 'Coberturas distintas o incompletas: la comparación no equivale a evaluar las mismas horas.' : 'Misma cantidad de horas registrada; las fechas coincidentes no están verificadas.'}</p>}
      </section>
      <details className="method-card"><summary>Definición, supuestos y tabla completa</summary>
        <div className="method-copy">
          <p><strong>{context?.periodo} · {context?.corte}</strong>. Cada semilla se conserva como una observación distinta. El skill corresponde a este período, no al último mes.</p>
          <p><strong>Captura sobre el oráculo del evaluador:</strong> comparación bajo los supuestos registrados; no es necesariamente el techo operable de un ciclo.</p>
          <pre>{context?.simulador ? JSON.stringify(context.simulador, null, 2) : 'Sin supuestos registrados; comparabilidad económica no verificada.'}</pre>
        </div>
        <div className="table-scroll"><Table>
          <TableHeader><TableRow>{['Modelo', 'Semilla', 'Estado', 'Horas', 'MAE (€/MWh)', 'Captura (%)', 'Mejora (%)', 'Pico ±1 h', 'Cobertura IC80', 'Calculado'].map(title => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{ranked.map(row => <TableRow key={`${row.model}:${row.seed}`}>
            <TableCell>{row.model}</TableCell><TableCell>{seed(row.seed)}</TableCell><TableCell>{row.estado ?? '—'}</TableCell><TableCell>{row.n_obs ?? '—'}</TableCell>
            <TableCell>{metric(row.mae)}</TableCell><TableCell>{metric(row.captura_pct, ' %')}</TableCell><TableCell>{metric(row.skill_vs_naive, ' %')}</TableCell>
            <TableCell>{metric(row.pico_1h_pct, ' %')}</TableCell><TableCell>{metric(row.cobertura_ic80, ' %')}</TableCell><TableCell>{row.calculado_en}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>
      </details>
    </>}
  </section><PerformanceHistory onSessionExpired={onSessionExpired} /></>;
}
