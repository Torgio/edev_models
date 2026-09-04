'use client';

import { useEffect, useState } from 'react';
import { addDays, format } from 'date-fns';
import { es } from 'date-fns/locale';
import {
  ArrowDownRight, ArrowUpRight, BatteryCharging, CalendarDays,
  ChevronLeft, ChevronRight, Clock3, Database, Sparkles,
  Zap,
} from 'lucide-react';
import {
  Area, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceDot, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts';

import { Button } from '@/components/ui/button';
import { TeamAccess } from '@/components/team-access';
import { AsistenteWidget } from '@/components/asistente-widget';
import { PeakAccuracy } from '@/components/peak-accuracy';
import { forecastMinimum } from '@/lib/forecast-minimum';
import { dailyPrice } from '@/lib/daily-price';
import { StoredEvaluations } from '@/components/stored-evaluations';
import { StoredBattery } from '@/components/stored-battery';
import { predictionUpdate } from '@/lib/prediction-update';
import { initialDashboardDay, type AvailableDay } from '@/lib/initial-day';
import { marketHourLabel } from '@/lib/market-hour';
import type { BatteryPayload } from '@/lib/battery-types';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';

const MODEL_STYLES = [
  { key: 'ensemble', label: 'Ensemble', color: '#e58b45' },
  { key: 'gru', label: 'GRU', color: '#43a99f' },
  { key: 'boosting', label: 'Boosting', color: '#7b8ee8' },
  { key: 'seq2seq', label: 'Seq2Seq', color: '#cf6f87' },
  { key: 'denso', label: 'Denso', color: '#b178d3' },
  { key: 'simplernn', label: 'SimpleRNN', color: '#d19a3a' },
  { key: 'conv1d_lstm', label: 'Conv1D-LSTM', color: '#4f8fbe' },
  { key: 'lstm', label: 'LSTM', color: '#829557' },
  { key: 'seq2seq_absoluto', label: 'Seq2Seq absoluto', color: '#bf685f' },
  { key: 'ensemble11', label: 'Ensemble 11', color: '#d8783e' },
  { key: 'lgbm_nucleo', label: 'LightGBM núcleo', color: '#4f9b68' },
  { key: 'lightgbm', label: 'LightGBM', color: '#6aa84f' },
  { key: 'xgboost', label: 'XGBoost', color: '#8f6ab8' },
] as const;

type ModelKey = string;
type PriceHour = Parameters<typeof dailyPrice>[0][number] & { hour: string };
type ChartRow = { datetime: string; hour: number; label: string; actual: number | null; consensusBand: [number, number] | null; predictions: Record<string, number> };
const API_URL = '/api/dashboard';
const priceFormat = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const averagePrice = (value: number | null | undefined) => value == null ? '—' : `${priceFormat.format(value)} €/MWh`;

function consensusBand(values: number[]): [number, number] | null {
  const ordered = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (ordered.length < 2) return null;
  const q1 = ordered[Math.floor((ordered.length - 1) * 0.25)];
  const q3 = ordered[Math.ceil((ordered.length - 1) * 0.75)];
  return [q1, q3];
}

function MetricCard({ icon: Icon, eyebrow, value, detail, tone = 'neutral' }: {
  icon: typeof Database; eyebrow: string; value: string; detail: string;
  tone?: 'neutral' | 'good' | 'warm';
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-icon"><Icon aria-hidden="true" /></div>
      <div><p>{eyebrow}</p><strong>{value}</strong><span>{detail}</span></div>
    </article>
  );
}

export default function Home() {
  return <TeamAccess>{(controls) => <Dashboard {...controls} />}</TeamAccess>;
}

function Dashboard({ username, onSessionExpired, onLogout }: { username: string | null; onSessionExpired: () => void; onLogout: () => Promise<void> }) {
  const [date, setDate] = useState(new Date());
  const day = format(date, 'yyyy-MM-dd');
  const [availableDays, setAvailableDays] = useState<AvailableDay[]>([]);
  const [visible, setVisible] = useState<ModelKey[]>([]);
  const [view, setView] = useState<'prediction' | 'evaluation' | 'battery' | 'assistant'>('prediction');
  const [referenceModel, setReferenceModel] = useState('');
  const [dayState, setDayState] = useState<{ day: string; hours: PriceHour[]; updated: string | null } | null>(null);
  const [dataStatus, setDataStatus] = useState<'loading' | 'live' | 'error'>('loading');
  const [batteryState, setBatteryState] = useState<{ day: string; data: BatteryPayload | null; status: 'loading' | 'ready' | 'error' }>({ day, data: null, status: 'loading' });
  const current = dayState?.day === day && dataStatus === 'live' ? dayState : null;
  const priceHours = current?.hours ?? [];
  const availableModels = [...new Set(priceHours.flatMap(point => Object.keys(point.predictions)))].sort();
  const MODELS = availableModels.map(key => MODEL_STYLES.find(model => model.key === key) ?? {
    key, label: key, color: `hsl(${[...key].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 0)} 45% 45%)`,
  });
  const selectedModel = availableModels.includes(referenceModel) ? referenceModel : availableModels.includes('ensemble') ? 'ensemble' : availableModels[0] ?? '';
  const currentMinimum = current ? forecastMinimum(priceHours, selectedModel) : null;
  const minimum = currentMinimum?.minimum;
  const averages = current ? dailyPrice(priceHours, day, selectedModel) : null;
  const comparisonPredicted = averages?.pairedHours ? averages.pairedPrediction : averages?.predicted;
  const comparisonActual = averages?.pairedReal;
  const data: ChartRow[] = priceHours.map((point, index) => ({
    datetime: point.datetime, hour: index, label: marketHourLabel(index), actual: point.actual,
    consensusBand: consensusBand(Object.values(point.predictions).filter((x): x is number => typeof x === 'number')),
    predictions: point.predictions as Record<string, number>,
  }));
  const hasActual = data.some(row => row.actual !== null);
  const selectedDayInfo = availableDays.find(item => item.date === day);
  const latestClosedDay = [...availableDays].reverse().find(item => item.closed)?.date;
  const actualHours = selectedDayInfo?.actual_hours;
  const expectedHours = selectedDayInfo?.expected_hours;
  const hasDayCoverage = Number.isInteger(actualHours) && Number.isInteger(expectedHours);
  const isClosed = hasDayCoverage && selectedDayInfo?.closed === true;
  const dayCoverageLabel = hasDayCoverage
    ? `${isClosed ? 'día cerrado' : actualHours ? 'cierre parcial' : 'precio real pendiente'} · ${actualHours}/${expectedHours} h reales`
    : format(date, 'yyyy');
  const plotted = visible.filter(key => availableModels.includes(key));
  const visibleModels = plotted.length ? plotted : availableModels.slice(0, 3);

  useEffect(() => {
    if (!API_URL) return;
    const controller = new AbortController();
    fetch(`${API_URL}/days?source=production`, { signal: controller.signal })
      .then((response) => {
        if (response.status === 401) onSessionExpired();
        if (!response.ok) throw new Error(`days ${response.status}`);
        return response.json() as Promise<{ days: AvailableDay[] }>;
      })
      .then((response) => {
        if (controller.signal.aborted) return;
        setAvailableDays(response.days);
        const initial = initialDashboardDay(response.days);
        if (initial) setDate(new Date(`${initial}T12:00:00`));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDataStatus('loading');
    setDayState(null);
    fetch(`${API_URL}/predictions/${day}?source=production`, { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (response.status === 401) onSessionExpired();
        if (!response.ok) throw new Error();
        return await response.json() as { date: string; hours: PriceHour[]; updated_at: string | null };
      }).then(response => {
        if (controller.signal.aborted) return;
        if (response.date !== day || !Array.isArray(response.hours)) throw new Error();
        setDayState({ day, hours: response.hours, updated: response.updated_at });
        setDataStatus('live');
      }).catch(() => {
        if (controller.signal.aborted) return;
        setDayState(null);
        setDataStatus('error');
      });
    return () => controller.abort();
  }, [day]);
  useEffect(() => {
    const controller = new AbortController();
    setBatteryState({ day, data: null, status: 'loading' });
    fetch(`${API_URL}/bess/${day}`, { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (response.status === 401) onSessionExpired();
      if (!response.ok) throw new Error();
      const payload = await response.json() as BatteryPayload;
      if (payload.date !== day || !Array.isArray(payload.plan) || !Array.isArray(payload.results)) throw new Error();
      if (!controller.signal.aborted) setBatteryState({ day, data: payload, status: 'ready' });
    }).catch(() => {
      if (!controller.signal.aborted) setBatteryState({ day, data: null, status: 'error' });
    });
    return () => controller.abort();
  }, [day]);
  const validReference = data.filter(row => Number.isFinite(row.predictions[selectedModel]));
  const peak = validReference.reduce<ChartRow | null>((best, row) => !best || row.predictions[selectedModel] > best.predictions[selectedModel] ? row : best, null);
  const max = peak?.predictions[selectedModel];
  const lastPredictionUpdate = predictionUpdate(current?.updated ?? null, current !== null);
  const currentBattery = batteryState.day === day ? batteryState : { day, data: null, status: 'loading' as const };
  const batteryPlan = currentBattery.data?.plan.filter(row => row.model === selectedModel) ?? [];
  const batteryMarks = batteryPlan.flatMap(row => {
    const index = data.findIndex(point => Date.parse(point.datetime) === Date.parse(row.datetime));
    if (index < 0) return [];
    if (row.carga_mw > 0) return [{ index, action: 'charge' as const, label: data[index].label }];
    if (row.descarga_mw > 0) return [{ index, action: 'discharge' as const, label: data[index].label }];
    return [];
  });
  const chargeLabels = batteryMarks.filter(mark => mark.action === 'charge').map(mark => mark.label);
  const dischargeLabels = batteryMarks.filter(mark => mark.action === 'discharge').map(mark => mark.label);

  function toggleModel(key: ModelKey) {
    setVisible(visibleModels.includes(key)
      ? visibleModels.length === 1 ? visibleModels : visibleModels.filter(item => item !== key)
      : [...visibleModels, key]);
  }

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><Zap aria-hidden="true" /></div>
          <div><p>TFM · Mercado eléctrico</p><h1>Pulso Energía</h1></div>
        </div>
        <nav aria-label="Secciones principales">
          <button type="button" className={view === 'prediction' ? 'active' : ''} aria-pressed={view === 'prediction'} onClick={() => setView('prediction')}>Predicción</button>
          <button type="button" className={view === 'evaluation' ? 'active' : ''} aria-pressed={view === 'evaluation'} onClick={() => setView('evaluation')}>Evaluación</button>
          <button type="button" className={view === 'battery' ? 'active' : ''} aria-pressed={view === 'battery'} onClick={() => setView('battery')}>BESS</button>
          <button type="button" className={view === 'assistant' ? 'active' : ''} aria-pressed={view === 'assistant'} onClick={() => setView('assistant')}>Asistente</button>
        </nav>
        <div className="system-status">
          <Clock3 size={16} aria-hidden="true" />
          <div><strong>Última actualización de predicciones</strong><span>{lastPredictionUpdate ?? 'Sin actualización confirmada'}</span></div>
        </div>
      </header>

      <section className="content-wrap" id="prevision">
        <div className="access-toolbar"><span>{username ? `Sesión: ${username}` : 'Acceso del equipo'}</span><Button variant="outline" size="sm" onClick={() => void onLogout()}>Cerrar sesión</Button></div>
        {view === 'prediction' ? <>
        <div className="page-heading">
          <div>
            <div className={`demo-pill status-${dataStatus}`}>
              {dataStatus === 'live' ? <Database aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
              {dataStatus === 'live' && 'Datos reales de producción'}
              {dataStatus === 'loading' && 'Consultando producción…'}
              {dataStatus === 'error' && 'Sin datos del día · consulta no disponible o sin registros'}
            </div>
            <p className="kicker">Predicción spot · España</p>
            <h2>{isClosed ? latestClosedDay === day ? 'Cómo fue el último día cerrado.' : 'Cómo fue este día cerrado.' : 'El precio previsto, modelo a modelo.'}</h2>
            <p className="intro">{isClosed
              ? `Compara lo previsto con las ${expectedHours} horas reales del mercado. Los días futuros con predicciones siguen disponibles con la flecha.`
              : 'Este día aún no tiene el precio real completo; muestra el plan previsto y deja pendientes los resultados.'}</p>
          </div>
          <div className="date-stepper" aria-label="Navegación por fecha">
            <Button variant="ghost" size="icon-lg" aria-label="Día anterior" onClick={() => setDate((day) => addDays(day, -1))}><ChevronLeft /></Button>
            <div><CalendarDays aria-hidden="true" /><span>{format(date, "EEEE, d 'de' MMMM", { locale: es })}</span><strong>{dayCoverageLabel}</strong></div>
            <Button variant="ghost" size="icon-lg" aria-label="Día siguiente" onClick={() => setDate((day) => addDays(day, 1))}><ChevronRight /></Button>
          </div>
        </div>

        <section className="forecast-workspace" aria-labelledby="forecast-title">
          <div className="forecast-main">
            <div className="forecast-heading">
              <div>
                <p className="section-label">Curva diaria · €/MWh</p>
                <h3 id="forecast-title">Previsión horaria</h3>
              </div>
              <label className="reference-control">Modelo de referencia
                <NativeSelect size="sm" value={selectedModel} disabled={!availableModels.length} onChange={event => setReferenceModel(event.target.value)}>
                  {!availableModels.length && <NativeSelectOption value="">Sin modelos</NativeSelectOption>}
                  {MODELS.map(model => <NativeSelectOption key={model.key} value={model.key}>{model.label}</NativeSelectOption>)}
                </NativeSelect>
              </label>
            </div>

            <div className="chart-toolbar">
              <details className="model-picker">
                <summary>Series visibles <strong>{visibleModels.length}</strong></summary>
                <div className="model-toggles" aria-label="Modelos visibles">
                  {MODELS.map(model => (
                    <button key={model.key} type="button" className={visibleModels.includes(model.key) ? 'selected' : ''}
                      onClick={() => toggleModel(model.key)} aria-pressed={visibleModels.includes(model.key)}>
                      <span style={{ background: model.color }} />{model.label}
                    </button>
                  ))}
                </div>
              </details>
              <div className="chart-legend">
                <span className="consensus-legend" title="Rango central entre modelos; no es un intervalo predictivo."><i />Dispersión central</span>
                {hasActual && <span className="actual-legend"><i />Precio real</span>}
                {batteryMarks.some(mark => mark.action === 'charge') && <span className="battery-overlay-legend charge"><i />Carga BESS</span>}
                {batteryMarks.some(mark => mark.action === 'discharge') && <span className="battery-overlay-legend discharge"><i />Descarga BESS</span>}
              </div>
            </div>

            {batteryMarks.length > 0 && <div className="operation-callout"><BatteryCharging aria-hidden="true" /><span>Plan guardado · {selectedModel}<strong>Cargar {chargeLabels.join(', ') || '—'} · descargar {dischargeLabels.join(', ') || '—'}</strong></span></div>}
            {!current && <div className="chart-empty" role="status">{dataStatus === 'loading' ? 'Consultando precios…' : 'No hay datos confirmados para esta fecha. No se muestran valores de demostración.'}</div>}
            <div className="chart-wrap" aria-label="Gráfico horario de predicciones por modelo">
              <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={340}>
                <LineChart data={data} margin={{ top: 28, right: 20, left: 4, bottom: 4 }}>
                  <CartesianGrid vertical={false} stroke="#e2e9e5" />
                  <XAxis dataKey="hour" tickFormatter={index => data[Number(index)]?.label ?? ''} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={20} tick={{ fill: '#66736f', fontSize: 12 }} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: '#66736f', fontSize: 12 }} domain={['dataMin - 8', 'dataMax + 8']} width={62} />
                  <Tooltip labelFormatter={index => data[Number(index)]?.label ?? ''} cursor={{ stroke: '#9aaba5', strokeDasharray: '3 4' }}
                    contentStyle={{ borderRadius: 14, border: '1px solid #d8e0dc', boxShadow: '0 12px 35px rgba(16,43,36,.12)' }}
                    formatter={(value, name) => [Array.isArray(value) ? `${Number(value[0]).toFixed(1)}–${Number(value[1]).toFixed(1)} €/MWh` : `${Number(value).toFixed(1)} €/MWh`, String(name)]} />
                  <Area type="monotone" dataKey="consensusBand" name="Dispersión central" stroke="none" fill="#43a99f" fillOpacity={0.12} activeDot={false} />
                  {batteryMarks.map(mark => <ReferenceArea key={`${mark.action}:${mark.index}`} x1={mark.index - .45} x2={mark.index + .45}
                    fill={mark.action === 'charge' ? '#43a99f' : '#e58b45'} fillOpacity={.14} strokeOpacity={0} ifOverflow="hidden" />)}
                  {MODELS.filter(model => visibleModels.includes(model.key)).map(model => (
                    <Line key={model.key} type="monotone" dataKey={(row: ChartRow) => row.predictions[model.key]} name={model.label}
                      stroke={model.color} strokeWidth={model.key === selectedModel ? 3 : 1.6} strokeOpacity={model.key === selectedModel ? 1 : .42}
                      dot={false} activeDot={{ r: 4 }} />
                  ))}
                  {hasActual && <Line type="monotone" dataKey="actual" name="Precio real" stroke="#142e28" strokeWidth={2.4} strokeDasharray="4 4" dot={false} />}
                  {minimum && <ReferenceDot x={minimum.index} y={minimum.value} r={5} fill="#e58b45" stroke="#142e28" ifOverflow="extendDomain"
                    label={{ value: 'Mínimo', position: 'top', fontSize: 11, fill: '#142e28' }} />}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="chart-caption">La banda representa el rango central entre los modelos recibidos; no es un intervalo de confianza. El modelo de referencia aparece destacado.</p>
          </div>

          <aside className="forecast-rail" aria-label="Indicadores del día">
            <div className="rail-heading"><p className="section-label">Lectura del día</p><h3>{selectedModel || 'Sin modelo'}</h3><span>{format(date, 'dd/MM/yyyy')}</span></div>
            <div className="price-comparison" aria-label="Comparación entre precio medio previsto y real">
              <div className="comparison-price predicted"><span>Precio medio previsto</span><strong>{averagePrice(comparisonPredicted)}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours} horas comunes` : averages?.predictedHours ? `${averages.predictedHours}/${averages.expectedHours} horas previstas` : 'Sin predicción'}</small></div>
              <div className={`comparison-delta ${averages?.difference == null ? 'pending' : averages.difference > 0 ? 'positive' : averages.difference < 0 ? 'negative' : 'neutral'}`}>
                <span>Δ previsto − real</span><strong>{averages?.difference == null ? '—' : `${averages.difference > 0 ? '+' : ''}${averagePrice(averages.difference)}`}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours}/${averages.expectedHours} comparables` : 'Pendiente de precio real'}</small>
              </div>
              <div className="comparison-price actual"><span>Precio medio real</span><strong>{averagePrice(comparisonActual)}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours} horas comunes` : 'Pendiente de cierre del mercado'}</small></div>
            </div>
            <div className="kpi-stack">
              <MetricCard icon={Database} eyebrow="Cobertura del día"
                value={averages ? `${averages.pairedHours}/${averages.expectedHours}` : '—'}
                detail="Horas con predicción y precio real" />
              <MetricCard icon={ArrowDownRight} eyebrow="Mínimo previsto"
                value={minimum ? `${minimum.value.toFixed(1)} €/MWh` : '—'} detail={minimum ? data[minimum.index]?.label ?? 'Sin datos' : 'Sin datos'} tone="warm" />
              <MetricCard icon={ArrowUpRight} eyebrow="Máximo previsto"
                value={max == null ? '—' : `${max.toFixed(1)} €/MWh`} detail={peak?.label ?? 'Sin datos'} tone="warm" />
            </div>
            <div className="peak-panel"><PeakAccuracy model={selectedModel} day={day} onSessionExpired={onSessionExpired} /></div>
          </aside>
        </section>

        <details className="audit-card" id="modelos">
          <summary>
            <span><Database aria-hidden="true" /><span><small>Detalle auditable</small><strong>Resultados por hora</strong></span></span>
            <em>{data.length} registros · {availableModels.length} modelos</em>
          </summary>
          <div className="table-scroll">
            <Table>
              <TableHeader><TableRow><TableHead>Hora mercado</TableHead>{MODELS.map(model => <TableHead key={model.key}>{model.label}</TableHead>)}<TableHead>Real</TableHead></TableRow></TableHeader>
              <TableBody>{data.map(row => (
                <TableRow key={row.hour} className={row.hour === minimum?.index || row.hour === peak?.hour ? 'highlight-row' : ''}>
                  <TableCell className="hour-cell">{row.label}</TableCell>
                  {MODELS.map(model => <TableCell key={model.key}>{row.predictions[model.key]?.toFixed(1) ?? '—'}</TableCell>)}
                  <TableCell>{row.actual === null ? '—' : row.actual.toFixed(1)}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          </div>
        </details>

        </> : view === 'evaluation' ? <div className="evaluation-view"><StoredEvaluations onSessionExpired={onSessionExpired} /></div> : view === 'battery' ?
          <div className="battery-view">
            <div className="view-datebar">
              <div><p className="kicker">Operación diaria</p><h2>Plan BESS guardado</h2><p>Consulta la decisión horaria y su resultado económico sin recalcular la estrategia.</p></div>
              <div className="date-stepper" aria-label="Navegación por fecha BESS">
                <Button variant="ghost" size="icon-lg" aria-label="Día anterior" onClick={() => setDate((day) => addDays(day, -1))}><ChevronLeft /></Button>
                <div><CalendarDays aria-hidden="true" /><span>{format(date, "EEEE, d 'de' MMMM", { locale: es })}</span><strong>{dayCoverageLabel}</strong></div>
                <Button variant="ghost" size="icon-lg" aria-label="Día siguiente" onClick={() => setDate((day) => addDays(day, 1))}><ChevronRight /></Button>
              </div>
            </div>
            <StoredBattery day={day} data={currentBattery.data} status={currentBattery.status} />
          </div> :
          <div className="assistant-view">
            <div className="assistant-heading">
              <p className="kicker">Consulta guiada · herramientas del proyecto</p>
              <h2>Pregunta a los datos de Pulso.</h2>
              <p>Consulta precios, predicciones, batería y metodología. La respuesta identifica la fuente utilizada y diferencia las funciones verificadas de las consultas dinámicas.</p>
            </div>
            <AsistenteWidget onSessionExpired={onSessionExpired} />
          </div>}
      </section>

      <footer><span><Zap aria-hidden="true" /> Pulso Energía · TFM UCM 2026</span><span>Datos en UTC · visualización Europe/Madrid</span></footer>
    </main>
  );
}
