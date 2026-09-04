'use client';

import { useEffect, useState } from 'react';
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Activity, CalendarCheck2, TrendingUp } from 'lucide-react';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import {
  clippedSkill, parsePerformanceIdentity, performanceIdentity, preferredPerformanceIdentity,
  type PerformanceOptionsPayload, type PerformancePayload, type PerformancePoint, type PerformanceModel,
} from '@/lib/performance-history';

const percent = (value: number | null) => value === null || !Number.isFinite(value)
  ? '—'
  : `${value > 0 ? '+' : ''}${value.toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
const modelLabel = (value: string) => value.replaceAll('_', ' ');
const dateLabel = (value: string) => new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', timeZone: 'UTC' })
  .format(new Date(`${value}T00:00:00Z`));

type ChartPoint = PerformancePoint & { chart_skill: number | null; chart_skill_7d: number | null };

function HistoryTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartPoint }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return <div className="history-tooltip">
    <strong>{dateLabel(row.date)}</strong><span>{row.n_obs} horas comparables · {row.estado}</span>
    <dl>
      <div><dt>Ventaja diaria</dt><dd className={(row.skill_vs_naive ?? 0) >= 0 ? 'good' : 'bad'}>{percent(row.skill_vs_naive)}</dd></div>
      <div><dt>MAE modelo</dt><dd>{row.mae.toLocaleString('es-ES', { maximumFractionDigits: 2 })} €/MWh</dd></div>
      <div><dt>MAE naive</dt><dd>{row.mae_naive.toLocaleString('es-ES', { maximumFractionDigits: 2 })} €/MWh</dd></div>
      <div><dt>Skill móvil 7d</dt><dd>{percent(row.skill_7d)}</dd></div>
    </dl>
  </div>;
}

export function PerformanceHistory({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [choice, setChoice] = useState(performanceIdentity('gru', 44));
  const [available, setAvailable] = useState<PerformanceModel[]>([]);
  const [optionsStatus, setOptionsStatus] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');
  const [payload, setPayload] = useState<PerformancePayload | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/dashboard/performance-options?source=production', { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (response.status === 401) onSessionExpired();
        if (!response.ok) throw new Error();
        return await response.json() as PerformanceOptionsPayload;
      })
      .then(result => {
        if (controller.signal.aborted || result.origin !== 'model_metrics_daily' || !Array.isArray(result.available)) return;
        setAvailable(result.available);
        if (!result.available.length) { setPayload(null); setOptionsStatus('empty'); return; }
        setChoice(current => preferredPerformanceIdentity(result.available, current));
        setOptionsStatus('ready');
      })
      .catch(() => {
        if (!controller.signal.aborted) { setAvailable([]); setPayload(null); setOptionsStatus('error'); }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (optionsStatus !== 'ready') return;
    const selected = parsePerformanceIdentity(choice);
    if (!selected) return;
    const controller = new AbortController();
    setStatus('loading');
    const query = new URLSearchParams({ model: selected.model, seed: String(selected.seed), days: '30', source: 'production' });
    fetch(`/api/dashboard/performance-history?${query}`, { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (response.status === 401) onSessionExpired();
        if (!response.ok) throw new Error();
        return await response.json() as PerformancePayload;
      })
      .then(result => {
        if (controller.signal.aborted || result.origin !== 'model_metrics_daily' || !Array.isArray(result.series)) return;
        setPayload(result); setStatus('ready');
      })
      .catch(() => { if (!controller.signal.aborted) { setPayload(null); setStatus('error'); } });
    return () => controller.abort();
  }, [choice, optionsStatus]);

  const summary = payload?.summary;
  const chart: ChartPoint[] = payload?.series.map(row => ({
    ...row, chart_skill: clippedSkill(row.skill_vs_naive), chart_skill_7d: clippedSkill(row.skill_7d),
  })) ?? [];
  const extremes = payload?.series.filter(row => row.skill_vs_naive !== null && Math.abs(row.skill_vs_naive) > 80) ?? [];

  return <section className="history-section" aria-labelledby="history-title">
    <div className="history-header">
      <div><p className="section-label">Rendimiento en el tiempo · fuente model_metrics_daily</p>
        <h2 id="history-title">¿Sigue mereciendo la pena el modelo?</h2>
        <p>Ventaja de error frente al precio de la misma hora del día anterior. Por encima de cero, el modelo mejora al naive.</p></div>
      {available.length ? <label>Modelo evaluado
        <NativeSelect value={choice} onChange={event => setChoice(event.target.value)}>
          {available.map(row => <NativeSelectOption key={performanceIdentity(row.model, row.seed)} value={performanceIdentity(row.model, row.seed)}>
            {modelLabel(row.model)} · semilla {row.seed === -1 ? 'N/A' : row.seed} · {row.days} días
          </NativeSelectOption>)}
        </NativeSelect>
      </label> : null}
    </div>

    {optionsStatus !== 'ready' || status !== 'ready' || !payload || !summary ? <div className="history-empty" role="status">
      {optionsStatus === 'loading' ? 'Consultando las series históricas disponibles…'
        : optionsStatus === 'empty' ? 'Todavía no hay métricas históricas guardadas.'
          : optionsStatus === 'error' ? 'No se pudieron consultar las métricas históricas.'
            : status === 'loading' ? 'Construyendo la serie desde las métricas guardadas…'
              : 'La serie elegida no está disponible. Puedes seleccionar otra combinación.'}
    </div> : <>
      <div className="history-kpis">
        <article><Activity aria-hidden="true" /><span>Ventaja · 30 días</span><strong className={(summary.skill_pct ?? 0) >= 0 ? 'good' : 'bad'}>{percent(summary.skill_pct)}</strong>
          <small>{summary.evaluated_days}/{summary.window_days} días · {summary.observations} horas</small></article>
        <article><TrendingUp aria-hidden="true" /><span>Últimos {summary.recent_days}</span><strong className={(summary.recent_skill_pct ?? 0) >= 0 ? 'good' : 'bad'}>{percent(summary.recent_skill_pct)}</strong>
          <small>{summary.recent_evaluated_days}/{summary.recent_days} días evaluados</small></article>
        <article><CalendarCheck2 aria-hidden="true" /><span>Días ganados</span><strong>{summary.days_won} / {summary.evaluated_days}</strong>
          <small>MAE del modelo menor que el naive</small></article>
      </div>

      <article className="history-chart-card">
        <div className="visual-heading"><div><p className="section-label">Ventaja diaria · %</p><h3>{modelLabel(payload.model)} frente al naive</h3></div>
          <div className="history-legend"><span className="win"><i />Gana</span><span className="loss"><i />Pierde</span><span className="rolling"><i />Media móvil 7d</span></div></div>
        {extremes.length > 0 && <div className="extreme-strip"><strong>Extremos fuera de escala:</strong>{extremes.slice(0, 4).map(row => <span key={row.date}>{dateLabel(row.date)} · {percent(row.skill_vs_naive)}</span>)}
          {extremes.length > 4 && <span>+{extremes.length - 4} días</span>}</div>}
        <div className="history-chart">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={360}>
            <ComposedChart data={chart} margin={{ top: 22, right: 18, bottom: 8, left: 6 }}>
              <CartesianGrid vertical={false} stroke="#e0e8e4" />
              <XAxis dataKey="date" tickFormatter={dateLabel} tickLine={false} axisLine={false} interval={2} tick={{ fontSize: 10, fill: '#71817b' }} />
              <YAxis domain={[-80, 80]} ticks={[-75, -50, -25, 0, 25, 50, 75]} tickFormatter={value => `${value > 0 ? '+' : ''}${value}%`}
                tickLine={false} axisLine={false} width={52} tick={{ fontSize: 10, fill: '#71817b' }} />
              <Tooltip content={<HistoryTooltip />} cursor={{ fill: 'rgba(36,72,62,.04)' }} />
              <ReferenceLine y={0} stroke="#24483e" strokeWidth={1.4} />
              <Bar dataKey="chart_skill" name="Ventaja diaria" maxBarSize={28} radius={[4, 4, 2, 2]}>
                {chart.map(row => <Cell key={row.date} fill={(row.skill_vs_naive ?? 0) >= 0 ? '#288568' : '#ad5b2b'} />)}
              </Bar>
              <Line type="monotone" dataKey="chart_skill_7d" name="Skill móvil 7d" stroke="#213d35" strokeWidth={2.2} dot={false} connectNulls={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="history-halves">
          <div className={(summary.first_half_skill_pct ?? 0) >= 0 ? 'good' : 'bad'}><span>Primera mitad</span><strong>{percent(summary.first_half_skill_pct)}</strong></div>
          <div className={(summary.second_half_skill_pct ?? 0) >= 0 ? 'good' : 'bad'}><span>Segunda mitad</span><strong>{percent(summary.second_half_skill_pct)}</strong></div>
        </div>
        <p className="history-caption">Las barras conservan el skill diario almacenado. La escala visual se limita a ±80 % para que un día extremo no oculte el resto; el tooltip mantiene el valor real. Los KPI agregan los dos MAE ponderados por horas, no promedian porcentajes diarios.</p>
        <details className="history-method"><summary>Cómo se calcula y qué significa el naive</summary>
          <p><strong>Skill agregado:</strong> {payload.definition} <strong>Naive:</strong> {payload.naive_rule ?? 'Regla no registrada.'}</p>
        </details>
      </article>

      <div className="history-insight">
        <strong>{(summary.first_half_skill_pct ?? 0) >= 0 && (summary.second_half_skill_pct ?? 0) < 0 ? 'La ventaja no se pierde de golpe.' : 'La señal cambia dentro de la ventana.'}</strong>
        <span>La comparación entre mitades deja visible si el modelo sigue ganando o si el régimen reciente ya se parece más al naive.</span>
      </div>
    </>}
  </section>;
}
