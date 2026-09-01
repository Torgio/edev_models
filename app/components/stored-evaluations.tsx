'use client';
import { useEffect, useState } from 'react';
import { Cell, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { bestMae, evaluationGroup, metric, numeric, rankedEvaluations, type Evaluation, type Order } from '@/lib/stored-evaluations';

const orderLabels: Record<Order, string> = {
  captura_pct: 'Captura',
  mae: 'MAE',
  skill_vs_naive: 'Skill vs. naive',
};
const modelColor = (model: string) => `hsl(${[...model].reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) % 360, 0)} 46% 46%)`;
const seed = (value: number) => value === -1 ? 'No aplica' : String(value);
const bestBy = (rows: Evaluation[], key: 'captura_pct' | 'skill_vs_naive') =>
  rows.filter(row => numeric(row[key])).sort((a, b) => b[key]! - a[key]!)[0];

function EvaluationTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: Evaluation }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return <div className="evaluation-tooltip">
    <strong>{row.model}</strong><span>Semilla {seed(row.seed)}</span>
    <dl><div><dt>MAE</dt><dd>{metric(row.mae, ' €/MWh')}</dd></div>
      <div><dt>Captura</dt><dd>{metric(row.captura_pct, ' %')}</dd></div>
      <div><dt>Skill</dt><dd>{metric(row.skill_vs_naive, ' %')}</dd></div></dl>
  </div>;
}

export function StoredEvaluations({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [rows, setRows] = useState<Evaluation[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [group, setGroup] = useState('');
  const [order, setOrder] = useState<Order>('captura_pct');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/dashboard/leaderboard', { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (response.status === 401) onSessionExpired();
      if (!response.ok) throw new Error();
      const result = await response.json() as { origin: string; models: Evaluation[] };
      if (result.origin !== 'model_metrics' || !Array.isArray(result.models)) throw new Error();
      if (!controller.signal.aborted) { setRows(result.models); setStatus('ready'); }
    }).catch(() => { if (!controller.signal.aborted) { setRows([]); setStatus('error'); } });
    return () => controller.abort();
  }, []);

  const groups = [...new Map(rows.map(row => [evaluationGroup(row), row])).entries()];
  const selected = groups.some(([key]) => key === group) ? group : groups[0]?.[0] ?? '';
  const ranked = rankedEvaluations(rows, selected, order);
  const scatter = ranked.filter(row => numeric(row.mae) && numeric(row.captura_pct));
  const context = ranked[0];
  const lowMae = bestMae(ranked);
  const highCapture = bestBy(ranked, 'captura_pct');
  const highSkill = bestBy(ranked, 'skill_vs_naive');

  return <section className="evaluation-section" id="hitos" aria-labelledby="evaluation-title">
    <div className="evaluation-header">
      <div><p className="section-label">Evidencia del TFM · fuente model_metrics</p><h2 id="evaluation-title">El error no ordena el valor económico</h2>
        <p>Compara cada modelo y semilla sin promediar resultados. El período evaluado permanece separado del día de previsión.</p></div>
      {groups.length > 0 && <label>Período y configuración
        <NativeSelect value={selected} onChange={event => setGroup(event.target.value)}>
          {groups.map(([key, row], index) => <NativeSelectOption key={key} value={key}>{row.periodo} · {row.corte} · configuración {index + 1}</NativeSelectOption>)}
        </NativeSelect>
      </label>}
    </div>

    {status !== 'ready' ? <div className="evaluation-empty" role="status">{status === 'loading' ? 'Consultando evaluaciones…' : 'Evaluaciones no disponibles. No se muestra un ranking de reserva.'}</div>
      : !rows.length ? <div className="evaluation-empty" role="status">No hay evaluaciones guardadas.</div> : <>
      <div className="evaluation-kpis">
        <article><span>Menor MAE</span><strong>{metric(lowMae?.mae, ' €/MWh')}</strong><small>{lowMae ? `${lowMae.model} · semilla ${seed(lowMae.seed)} · ${lowMae.n_obs ?? '—'} observaciones` : 'Sin valor guardado'}</small></article>
        <article><span>Mayor captura registrada</span><strong>{metric(highCapture?.captura_pct, ' %')}</strong><small>{highCapture ? `${highCapture.model} · semilla ${seed(highCapture.seed)}` : 'Sin valor guardado'}</small></article>
        <article><span>Mayor skill vs. naive</span><strong>{metric(highSkill?.skill_vs_naive, ' %')}</strong><small>{highSkill ? `${highSkill.model} · semilla ${seed(highSkill.seed)}` : 'Sin valor guardado'}</small></article>
      </div>

      <div className="evaluation-grid">
        <article className="scatter-card">
          <div className="visual-heading"><div><p className="section-label">Una marca por modelo y semilla</p><h3>MAE frente a captura económica</h3></div><span>{scatter.length} evaluaciones comparables</span></div>
          <div className="scatter-wrap">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={320}>
              <ScatterChart margin={{ top: 22, right: 24, bottom: 30, left: 12 }}>
                <CartesianGrid stroke="#e1e8e4" strokeDasharray="3 4" />
                <XAxis type="number" dataKey="mae" name="MAE" domain={['dataMin - 1', 'dataMax + 1']} tick={{ fontSize: 11, fill: '#667770' }} tickLine={false}
                  label={{ value: 'MAE (€/MWh) · menor es mejor', position: 'bottom', offset: 12, fontSize: 11, fill: '#52685e' }} />
                <YAxis type="number" dataKey="captura_pct" name="Captura" domain={['dataMin - 2', 'dataMax + 2']} width={58} tick={{ fontSize: 11, fill: '#667770' }} tickLine={false}
                  label={{ value: 'Captura registrada (%)', angle: -90, position: 'insideLeft', fontSize: 11, fill: '#52685e' }} />
                <Tooltip content={<EvaluationTooltip />} cursor={{ strokeDasharray: '3 4' }} />
                <Scatter data={scatter} isAnimationActive={false}>{scatter.map(row => <Cell key={`${row.model}:${row.seed}`} fill={modelColor(row.model)} />)}</Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p>La captura compara el ingreso del modelo con el oráculo definido por el evaluador y sus supuestos registrados. No representa necesariamente el máximo operable de una batería.</p>
        </article>

        <aside className="ranking-card">
          <div className="visual-heading"><div><p className="section-label">Cinco primeras filas</p><h3>Ranking visual</h3></div></div>
          <div className="ranking-tabs" role="group" aria-label="Orden del ranking">
            {(Object.keys(orderLabels) as Order[]).map(key => <button type="button" key={key} aria-pressed={order === key}
              className={order === key ? 'active' : ''} onClick={() => setOrder(key)}>{orderLabels[key]}</button>)}
          </div>
          <ol className="visual-ranking">
            {ranked.slice(0, 5).map((row, index) => <li key={`${row.model}:${row.seed}`}>
              <span className="rank-number">{index + 1}</span><i style={{ background: modelColor(row.model) }} />
              <span><strong>{row.model}</strong><small>Semilla {seed(row.seed)} · {row.estado ?? 'sin estado'}</small></span>
              <b>{order === 'mae' ? metric(row.mae) : order === 'captura_pct' ? metric(row.captura_pct, ' %') : metric(row.skill_vs_naive, ' %')}</b>
            </li>)}
          </ol>
          <p>El orden cambia con la métrica. No implica que el primer modelo esté adoptado.</p>
        </aside>
      </div>

      <details className="method-card"><summary>Definición, supuestos y tabla completa</summary>
        <div className="method-copy">
          <p><strong>{context?.periodo} · {context?.corte}</strong>. Cada semilla se conserva como una observación distinta. El skill corresponde a este período, no al último mes.</p>
          <p><strong>Captura sobre el oráculo del evaluador:</strong> comparación bajo los supuestos registrados; no es necesariamente el techo operable de un ciclo.</p>
          <pre>{context?.simulador ? JSON.stringify(context.simulador, null, 2) : 'Sin supuestos registrados; comparabilidad económica no verificada.'}</pre>
        </div>
        <div className="table-scroll"><Table>
          <TableHeader><TableRow>{['Modelo', 'Semilla', 'Estado', 'Observaciones', 'MAE', 'Captura', 'Skill', 'Pico ±1 h', 'Cobertura IC80', 'Calculado'].map(title => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{ranked.map(row => <TableRow key={`${row.model}:${row.seed}`}>
            <TableCell>{row.model}</TableCell><TableCell>{seed(row.seed)}</TableCell><TableCell>{row.estado ?? '—'}</TableCell><TableCell>{row.n_obs ?? '—'}</TableCell>
            <TableCell>{metric(row.mae)}</TableCell><TableCell>{metric(row.captura_pct, ' %')}</TableCell><TableCell>{metric(row.skill_vs_naive, ' %')}</TableCell>
            <TableCell>{metric(row.pico_1h_pct, ' %')}</TableCell><TableCell>{metric(row.cobertura_ic80, ' %')}</TableCell><TableCell>{row.calculado_en}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>
      </details>
    </>}
  </section>;
}
