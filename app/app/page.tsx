'use client';

import { useEffect, useState } from 'react';
import { addDays, format } from 'date-fns';
import { es } from 'date-fns/locale';
import {
  ArrowDownRight, ArrowUpRight, Bell, CalendarDays,
  ChevronLeft, ChevronRight, Clock3, Database, FileDown,
  GitCompareArrows, TrendingUp, TriangleAlert, Zap,
} from 'lucide-react';
import {
  Area, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { marketWindows } from '@/lib/market-summary';
import { marketAlerts, pairedDifference, preferredPriceSeries } from '@/lib/market-alerts';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { TeamAccess } from '@/components/team-access';
import { AsistenteWidget } from '@/components/asistente-widget';
import { PeakAccuracy } from '@/components/peak-accuracy';
import { forecastMinimum } from '@/lib/forecast-minimum';
import { dailyPrice } from '@/lib/daily-price';
import { StoredEvaluations } from '@/components/stored-evaluations';
import { StoredBattery } from '@/components/stored-battery';
import { BatteryStudy } from '@/components/battery-study';
import { predictionUpdate } from '@/lib/prediction-update';
import { initialDashboardDay, type AvailableDay } from '@/lib/initial-day';
import { marketHourClockLabel } from '@/lib/market-hour';
import { MODEL_STYLES, modelColor } from '@/lib/model-color';
import { forecastRamp, negativePriceHours } from '@/lib/market-signals';
import { priceAxisLower, priceAxisTick, priceAxisUpper } from '@/lib/price-axis';
import { formatEnergyPrice } from '@/lib/price-format';
import { modelsToPlot } from '@/lib/visible-models';
import type { BatteryPayload } from '@/lib/battery-types';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';

type ModelKey = string;
type PriceHour = Parameters<typeof dailyPrice>[0][number] & { hour: string };
type ChartRow = { datetime: string; hour: number; label: string; actual: number | null; marketValue: number | null; comparison: number | null; dayDifference: number | null; consensusBand: [number, number] | null; predictions: Record<string, number> };
const API_URL = '/api/dashboard';

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

function DateNavigator({ date, days, coverageLabel, ariaLabel, onChange }: {
  date: Date; days: AvailableDay[]; coverageLabel: string; ariaLabel: string; onChange: (date: Date) => void;
}) {
  const [open, setOpen] = useState(false);
  const available = new Set(days.map(item => item.date));
  const closed = days.filter(item => item.closed).map(item => new Date(`${item.date}T12:00:00`));
  const partial = days.filter(item => !item.closed && item.actual_hours > 0).map(item => new Date(`${item.date}T12:00:00`));
  const pending = days.filter(item => !item.actual_hours).map(item => new Date(`${item.date}T12:00:00`));
  const selected = format(date, 'yyyy-MM-dd');
  const sortedDays = [...available].sort();
  const previous = sortedDays.filter(day => day < selected).at(-1);
  const next = sortedDays.find(day => day > selected);
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const tomorrow = format(addDays(new Date(`${today}T12:00:00`), 1), 'yyyy-MM-dd');
  const latestClosed = days.filter(day => day.closed).map(day => day.date).sort().at(-1);
  const shortcuts = [{ label: 'Hoy', day: today }, { label: 'Mañana', day: tomorrow }, { label: 'Último día cerrado', day: latestClosed }];
  return <div className="date-navigation"><div className="date-stepper" aria-label={ariaLabel}>
    <Button variant="ghost" size="icon-lg" aria-label="Día anterior disponible" disabled={!previous} onClick={() => previous && onChange(new Date(`${previous}T12:00:00`))}><ChevronLeft /></Button>
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className="date-picker-trigger" aria-label="Elegir fecha en el calendario">
        <CalendarDays aria-hidden="true" />
        <span>{format(date, "d 'de' MMMM yyyy", { locale: es })}</span>
        <strong>{coverageLabel}</strong>
      </PopoverTrigger>
      <PopoverContent className="date-calendar-popover" align="center" sideOffset={8}>
        <Calendar key={format(date, 'yyyy-MM-dd')} labels={{ labelPrevious: () => 'Mes anterior', labelNext: () => 'Mes siguiente', labelDayButton: (date, modifiers) => `${format(date, "EEEE, d 'de' MMMM yyyy", { locale: es })}${modifiers.today ? ', hoy' : ''}${modifiers.selected ? ', seleccionado' : ''}` }} mode="single" selected={date} defaultMonth={date} locale={es}
          disabled={candidate => !available.has(format(candidate, 'yyyy-MM-dd'))}
          modifiers={{ closed, partial, pending }}
          modifiersClassNames={{ closed: 'calendar-day-closed', partial: 'calendar-day-partial', pending: 'calendar-day-pending' }}
          onSelect={candidate => {
            if (!candidate || !available.has(format(candidate, 'yyyy-MM-dd'))) return;
            onChange(candidate); setOpen(false);
          }} />
        <div className="calendar-status-legend" aria-label="Estado de las fechas">
          <span className="closed">Cerrado</span><span className="partial">Parcial</span><span className="pending">Previsto</span>
        </div>
      </PopoverContent>
    </Popover>
    <Button variant="ghost" size="icon-lg" aria-label="Día siguiente disponible" disabled={!next} onClick={() => next && onChange(new Date(`${next}T12:00:00`))}><ChevronRight /></Button>
  </div><div className="date-shortcuts" aria-label="Accesos rápidos por fecha">
    {shortcuts.map(shortcut => <Button key={shortcut.label} variant="ghost" size="sm" disabled={!shortcut.day || !available.has(shortcut.day)}
      aria-pressed={selected === shortcut.day} onClick={() => shortcut.day && onChange(new Date(`${shortcut.day}T12:00:00`))}>{shortcut.label}</Button>)}
  </div></div>;
}

export default function Home() {
  return <TeamAccess>{(controls) => <Dashboard {...controls} />}</TeamAccess>;
}

function Dashboard({ username, onSessionExpired, onLogout }: { username: string | null; onSessionExpired: () => void; onLogout: () => Promise<void> }) {
  const [retry, setRetry] = useState(0);
  const [daysRetry, setDaysRetry] = useState(0);
  const [daysError, setDaysError] = useState(false);
  const [date, setDate] = useState(new Date());
  const day = format(date, 'yyyy-MM-dd');
  const [availableDays, setAvailableDays] = useState<AvailableDay[]>([]);
  const [visible, setVisible] = useState<ModelKey[]>([]);
  const [view, setView] = useState<'prediction' | 'evaluation' | 'battery' | 'assistant'>('prediction');
  const [batteryView, setBatteryView] = useState<'daily' | 'study'>('daily');
  const [compareModels, setCompareModels] = useState(false);
  const [comparisonChoice, setComparisonChoice] = useState('previous');
  const [comparisonState, setComparisonState] = useState<{ day: string; hours: PriceHour[]; status: 'loading' | 'ready' | 'error' } | null>(null);
  const [selectedAlert, setSelectedAlert] = useState<string | null>(null);
  const [reportGeneratedAt, setReportGeneratedAt] = useState<string | null>(null);
  const [referenceModel, setReferenceModel] = useState('');
  const [dayState, setDayState] = useState<{ day: string; hours: PriceHour[]; updated: string | null } | null>(null);
  const [dataStatus, setDataStatus] = useState<'loading' | 'live' | 'error'>('loading');
  const [batteryState, setBatteryState] = useState<{ day: string; data: BatteryPayload | null; status: 'loading' | 'ready' | 'error' }>({ day, data: null, status: 'loading' });
  const current = dayState?.day === day && dataStatus === 'live' ? dayState : null;
  const priceHours = current?.hours ?? [];
  const availableModels = [...new Set(priceHours.flatMap(point => Object.keys(point.predictions)))].sort();
  const MODELS = availableModels.map(key => MODEL_STYLES.find(model => model.key === key) ?? {
    key, label: key, color: modelColor(key),
  });
  const selectedModel = availableModels.includes(referenceModel) ? referenceModel : availableModels.includes('ensemble') ? 'ensemble' : availableModels[0] ?? '';
  const currentMinimum = current ? forecastMinimum(priceHours, selectedModel) : null;
  const minimum = currentMinimum?.minimum;
  const averages = current ? dailyPrice(priceHours, day, selectedModel) : null;
  const comparisonPredicted = averages?.pairedHours ? averages.pairedPrediction : averages?.predicted;
  const comparisonActual = averages?.pairedReal;
  const sortedDates = availableDays.map(item => item.date).sort();
  const previousDay = sortedDates.filter(item => item < day).at(-1) ?? null;
  const weekDay = sortedDates.includes(format(addDays(date, -7), 'yyyy-MM-dd')) ? format(addDays(date, -7), 'yyyy-MM-dd') : null;
  const comparisonDay = comparisonChoice === 'none' ? null : comparisonChoice === 'previous' ? previousDay : comparisonChoice === 'week' ? weekDay : comparisonChoice.startsWith('date:') ? comparisonChoice.slice(5) : null;
  const comparisonHours = comparisonState?.status === 'ready' && comparisonState.day === comparisonDay ? comparisonState.hours : [];
  const currentSeries = preferredPriceSeries(priceHours, selectedModel);
  const comparedSeries = preferredPriceSeries(comparisonHours, selectedModel);
  const data: ChartRow[] = priceHours.map((point, index) => ({
    datetime: point.datetime, hour: index, label: marketHourClockLabel(index, point.hour), actual: point.actual,
    marketValue: currentSeries.values[index] ?? null,
    comparison: comparedSeries.values[index] ?? null,
    dayDifference: Number.isFinite(currentSeries.values[index]) && Number.isFinite(comparedSeries.values[index])
      ? currentSeries.values[index]! - comparedSeries.values[index]!
      : null,
    consensusBand: consensusBand(Object.values(point.predictions).filter((x): x is number => typeof x === 'number')),
    predictions: point.predictions as Record<string, number>,
  }));
  const hasActual = data.some(row => row.actual !== null);
  const selectedDayInfo = availableDays.find(item => item.date === day);
  const actualHours = selectedDayInfo?.actual_hours;
  const expectedHours = selectedDayInfo?.expected_hours;
  const hasDayCoverage = Number.isInteger(actualHours) && Number.isInteger(expectedHours);
  const isClosed = hasDayCoverage && selectedDayInfo?.closed === true;
  const dayCoverageLabel = hasDayCoverage
    ? `${isClosed ? 'día cerrado' : actualHours ? 'cierre parcial' : 'precio real pendiente'} · ${actualHours}/${expectedHours} h reales`
    : format(date, 'yyyy');
  const visibleModels = modelsToPlot(availableModels, visible, selectedModel);
  const plottedModels = compareModels ? visibleModels : [selectedModel];
  const windows = marketWindows(priceHours, day, selectedModel);
  const comparisonResult = pairedDifference(currentSeries.values, comparedSeries.values);
  const alerts = marketAlerts(currentSeries.values, comparedSeries.values);
  const activeAlert = alerts.find(alert => alert.id === selectedAlert) ?? null;
  const selectedDayLabel = format(new Date(`${day}T12:00:00`), 'd MMM yyyy', { locale: es });
  const comparisonDayLabel = comparisonDay ? format(new Date(`${comparisonDay}T12:00:00`), 'd MMM yyyy', { locale: es }) : null;

  useEffect(() => {
    if (!API_URL) return;
    setDaysError(false);
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
      .catch(() => { if (!controller.signal.aborted) setDaysError(true); });
    return () => controller.abort();
  }, [daysRetry]);

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
  }, [day, retry]);
  useEffect(() => {
    setSelectedAlert(null);
    if (view !== 'prediction' || !comparisonDay) { setComparisonState(null); return; }
    const controller = new AbortController();
    setComparisonState({ day: comparisonDay, hours: [], status: 'loading' });
    fetch(`${API_URL}/predictions/${comparisonDay}?source=production`, { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (response.status === 401) onSessionExpired();
        if (!response.ok) throw new Error();
        return await response.json() as { date: string; hours: PriceHour[] };
      }).then(response => {
        if (!controller.signal.aborted && response.date === comparisonDay && Array.isArray(response.hours)) setComparisonState({ day: comparisonDay, hours: response.hours, status: 'ready' });
      }).catch(() => { if (!controller.signal.aborted) setComparisonState({ day: comparisonDay, hours: [], status: 'error' }); });
    return () => controller.abort();
  }, [comparisonDay, retry, view]);
  useEffect(() => {
    if (view !== 'battery' || batteryView !== 'daily') return;
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
  }, [day, view, batteryView, retry]);
  const validReference = data.filter(row => Number.isFinite(row.predictions[selectedModel]));
  const peak = validReference.reduce<ChartRow | null>((best, row) => !best || row.predictions[selectedModel] > best.predictions[selectedModel] ? row : best, null);
  const max = peak?.predictions[selectedModel];
  const ramp = forecastRamp(data, selectedModel);
  const negatives = negativePriceHours(data, selectedModel);
  const lastPredictionUpdate = predictionUpdate(current?.updated ?? null, current !== null);
  const currentBattery = batteryState.day === day ? batteryState : { day, data: null, status: 'loading' as const };

  function toggleModel(key: ModelKey) {
    if (key === selectedModel) return;
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
        {daysError && <div className="study-notice" role="alert">No se pudieron consultar las fechas disponibles. <Button variant="outline" onClick={() => setDaysRetry(value => value + 1)}>Reintentar calendario</Button></div>}
        {view === 'prediction' ? <>
        <header className="print-report-header" aria-hidden="true">
          <div className="print-report-brand"><span><Zap /></span><div><small>TFM · Mercado eléctrico</small><strong>Pulso Energía</strong></div></div>
          <div className="print-report-title"><small>Informe diario de mercado</small><h2>{selectedDayLabel}</h2><p>Resumen de precios, previsión, comparación y alertas</p></div>
          <dl>
            <div><dt>Modelo</dt><dd>{selectedModel || 'Sin modelo'}</dd></div>
            <div><dt>Comparación</dt><dd>{comparisonDayLabel ?? 'Sin comparación'}</dd></div>
            <div><dt>Estado de datos</dt><dd>{isClosed ? 'Día cerrado' : actualHours ? 'Cierre parcial' : 'Precio real pendiente'}</dd></div>
            <div><dt>Generado</dt><dd>{reportGeneratedAt ?? 'Al exportar'}</dd></div>
          </dl>
          <p className="print-report-note">Informe académico · TFM UCM 2026 · Datos en UTC, visualización Europe/Madrid</p>
        </header>
        <div className="page-heading market-heading">
          <div>
            <p className="kicker">Mercado eléctrico · España</p>
            <h2>Precios del mercado</h2>
            <p className="intro" role="status">{dataStatus === 'loading' ? 'Consultando precios…' : dataStatus === 'error' ? 'No se pudo obtener el precio de este día.' : isClosed ? 'Día cerrado · previsión y precio real disponibles' : 'Previsión · precio real pendiente de completar'}</p>
            <p className="market-updated">Actualizado: {lastPredictionUpdate ?? 'sin actualización confirmada'}</p>
          </div>
          <div className="market-heading-actions">
            <DateNavigator date={date} days={availableDays} coverageLabel={dayCoverageLabel} ariaLabel="Navegación por fecha" onChange={setDate} />
            <Button className="pdf-export-button" variant="outline" onClick={() => {
              setReportGeneratedAt(format(new Date(), 'dd/MM/yyyy, HH:mm'));
              window.setTimeout(() => window.print(), 0);
            }} disabled={!current}>
              <FileDown aria-hidden="true" /> Exportar informe PDF
            </Button>
          </div>
        </div>

        <section className="market-summary" aria-label="Resumen operativo previsto" aria-live="polite">
          <article><span>Tramo más barato · 3 h</span><strong>{windows.cheapest?.label ?? '—'}</strong><p>{windows.cheapest ? `${formatEnergyPrice(windows.cheapest.average)} de media` : dataStatus === 'loading' ? 'Consultando previsión…' : 'Sin tres horas consecutivas disponibles'}</p></article>
          <article><span>Tramo más caro · 3 h</span><strong>{windows.priciest?.label ?? '—'}</strong><p>{windows.priciest ? `${formatEnergyPrice(windows.priciest.average)} de media` : dataStatus === 'loading' ? 'Consultando previsión…' : 'Sin tres horas consecutivas disponibles'}</p></article>
        </section>
        <p className="market-summary-note">Previsión de {selectedModel || 'modelo pendiente'} · tramos de 3 horas entre los datos disponibles ({windows.covered}/{windows.expected} h).</p>

        <section className="day-comparison-summary" aria-label="Comparación entre días" aria-live="polite">
          <div><span>Día analizado</span><strong>{selectedDayLabel}</strong><small>Precio real y previsión · {selectedModel || 'sin modelo'}</small></div>
          <div><span>{comparisonDayLabel ? `Frente al ${comparisonDayLabel}` : 'Comparación'}</span><strong>{comparisonResult.difference == null ? '—' : formatEnergyPrice(comparisonResult.difference, { sign: true })}</strong><small>{comparisonResult.hours ? `${comparisonResult.hours} horas comparables` : comparisonState?.status === 'loading' ? 'Consultando…' : 'Sin comparación disponible'}</small></div>
          <label>Comparar con
            <NativeSelect value={comparisonChoice} onChange={event => setComparisonChoice(event.target.value)}>
              <NativeSelectOption value="none">Sin comparación</NativeSelectOption>
              <NativeSelectOption value="previous" disabled={!previousDay}>Día anterior disponible</NativeSelectOption>
              <NativeSelectOption value="week" disabled={!weekDay}>Mismo día de la semana anterior</NativeSelectOption>
              {sortedDates.filter(item => item !== day).slice(-14).reverse().map(item => <NativeSelectOption key={item} value={`date:${item}`}>{format(new Date(`${item}T12:00:00`), 'd MMM yyyy', { locale: es })}</NativeSelectOption>)}
            </NativeSelect>
          </label>
        </section>

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
              <div className="chart-view-controls"><label><Checkbox checked={compareModels} onCheckedChange={setCompareModels} />Comparar modelos</label></div>
              {compareModels && <details className="model-picker">
                <summary>Series visibles <strong>{visibleModels.length}</strong></summary>
                <div className="model-toggles" aria-label="Modelos visibles">
                  {MODELS.map(model => (
                    <button key={model.key} type="button" className={`${visibleModels.includes(model.key) ? 'selected' : ''} ${model.key === selectedModel ? 'reference-series' : ''}`}
                      onClick={() => toggleModel(model.key)} aria-pressed={visibleModels.includes(model.key)}
                      disabled={model.key === selectedModel} title={model.key === selectedModel ? 'El modelo de referencia siempre permanece visible' : undefined}>
                      <span style={{ background: model.color }} />{model.label}
                    </button>
                  ))}
                </div>
              </details>}
              <div className="chart-legend">
                {compareModels && <span className="consensus-legend"><i />Dispersión central</span>}
                {selectedModel && <span className="forecast-day-legend"><i style={{ borderColor: modelColor(selectedModel) }} />{selectedDayLabel} · previsión {selectedModel}</span>}
                {hasActual && <span className="actual-legend"><i />{selectedDayLabel} · precio real</span>}
                {comparisonDayLabel && comparisonState?.status === 'ready' && <span className="comparison-day-legend"><i />{comparisonDayLabel} · {comparedSeries.kind === 'real' ? 'precio real' : `previsión ${selectedModel}`}</span>}
                {negatives.entries.length > 0 && <span className="negative-price-legend"><i />Horas bajo cero</span>}
              </div>
            </div>


            {!current && <div className="chart-empty" role="status">{dataStatus === 'loading' ? 'Consultando precios…' : 'No se pudieron obtener datos para esta fecha.'}{dataStatus === 'error' && <Button variant="outline" onClick={() => setRetry(value => value + 1)}>Reintentar</Button>}</div>}
            <div className="market-alert-layout">
            <div className="chart-wrap" aria-label="Gráfico horario de predicciones por modelo y día comparado">
              <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={340}>
                <LineChart data={data} margin={{ top: 28, right: 20, left: 4, bottom: 4 }}>
                  <CartesianGrid vertical={false} stroke="#e2e9e5" />
                  <XAxis dataKey="hour" tickFormatter={index => data[Number(index)]?.label ?? ''} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={48} tick={{ fill: '#52685e', fontSize: 12 }} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: '#66736f', fontSize: 12 }}
                    domain={[priceAxisLower, priceAxisUpper]} allowDecimals={false} tickFormatter={priceAxisTick} width={62} />
                  <Tooltip labelFormatter={index => data[Number(index)]?.label ?? ''} cursor={{ stroke: '#9aaba5', strokeDasharray: '3 4' }}
                    contentStyle={{ borderRadius: 14, border: '1px solid #d8e0dc', boxShadow: '0 12px 35px rgba(16,43,36,.12)' }}
                    formatter={(value, name) => [Array.isArray(value)
                      ? `${formatEnergyPrice(Number(value[0]), { unit: false })}–${formatEnergyPrice(Number(value[1]))}`
                      : formatEnergyPrice(Number(value), { sign: String(name).startsWith('Diferencia ') }), String(name)]} />
                  {compareModels && <Area type="monotone" dataKey="consensusBand" name="Dispersión central" stroke="none" fill="#43a99f" fillOpacity={0.12} activeDot={false} />}
                  <ReferenceLine y={0} stroke="#aab6b1" strokeDasharray="3 4" />
                  {negatives.entries.map(mark => <ReferenceArea key={`negative:${mark.index}`} x1={mark.index - .45} x2={mark.index + .45}
                    fill={mark.actual ? '#b85f3b' : '#e8a36f'} fillOpacity={mark.actual && mark.predicted ? .16 : .1} strokeOpacity={0} ifOverflow="hidden" />)}
                  {ramp && <ReferenceArea x1={ramp.from} x2={ramp.to} fill="#6689a8" fillOpacity={.08} strokeOpacity={0} ifOverflow="hidden" />}
                  {activeAlert && <ReferenceArea x1={activeAlert.from - .45} x2={activeAlert.to + .45} fill="#e58b45" fillOpacity={.24} stroke="#b9662f" strokeWidth={1.5} strokeOpacity={.9} ifOverflow="hidden" />}
                  {MODELS.filter(model => plottedModels.includes(model.key)).map(model => (
                    <Line key={model.key} type="monotone" dataKey={(row: ChartRow) => row.predictions[model.key]} name={`${selectedDayLabel} · previsión ${model.label}`}
                      stroke={model.color} strokeWidth={model.key === selectedModel ? 3 : 1.6} strokeOpacity={model.key === selectedModel ? 1 : .42}
                      dot={false} activeDot={{ r: 4 }} />
                  ))}
                  {hasActual && <Line type="monotone" dataKey="actual" name={`${selectedDayLabel} · precio real`} stroke="#142e28" strokeWidth={2.4} strokeDasharray="4 4" dot={false} />}
                  {comparisonDayLabel && <Line type="monotone" dataKey="comparison" name={`${comparisonDayLabel} · ${comparedSeries.kind === 'real' ? 'precio real' : `previsión ${selectedModel}`}`} stroke="#6689a8" strokeWidth={2} strokeDasharray="7 5" dot={false} activeDot={{ r: 4 }} />}
                  {comparisonDayLabel && <Line type="linear" dataKey="dayDifference" name={`Diferencia ${selectedDayLabel} − ${comparisonDayLabel}`} stroke="transparent" strokeWidth={0} dot={false} activeDot={false} legendType="none" />}
                  {activeAlert && <ReferenceLine x={activeAlert.to} stroke="#b9662f" strokeWidth={2} strokeDasharray="3 3"
                    label={{ value: activeAlert.title, position: 'insideTopRight', fontSize: 11, fontWeight: 700, fill: '#8b4b26' }} />}
                  {activeAlert && Number.isFinite(data[activeAlert.to]?.marketValue) && <ReferenceDot x={activeAlert.to} y={data[activeAlert.to].marketValue!} r={7} fill="#e58b45" stroke="#8b4b26" strokeWidth={2} ifOverflow="extendDomain" />}
                  {minimum && <ReferenceDot x={minimum.index} y={minimum.value} r={5} fill="#e58b45" stroke="#142e28" ifOverflow="extendDomain"
                    label={{ value: 'Valle previsto', position: 'top', fontSize: 11, fill: '#142e28' }} />}
                  {ramp && <ReferenceDot x={ramp.to} y={ramp.value} r={4} fill="#6689a8" stroke="#fff" ifOverflow="extendDomain" />}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <aside className="market-alerts" aria-labelledby="market-alerts-title">
              <div className="market-alerts-heading"><div><p className="section-label">Lectura automática</p><h3 id="market-alerts-title">Alertas del día</h3></div><span>{alerts.length}</span></div>
              {alerts.length ? alerts.map(alert => {
                const Icon = alert.id === 'peak' ? TrendingUp : alert.id === 'difference' ? GitCompareArrows : alert.id === 'negative' ? TriangleAlert : ArrowUpRight;
                return <button key={alert.id} type="button" aria-pressed={activeAlert?.id === alert.id} onClick={() => setSelectedAlert(current => current === alert.id ? null : alert.id)}>
                  <Icon aria-hidden="true" /><span><strong>{alert.title}</strong><small>{data[alert.from]?.label}{alert.to !== alert.from ? `–${data[alert.to]?.label}` : ''} · {alert.detail}</small></span>
                </button>;
              }) : <div className="market-alerts-empty"><Bell aria-hidden="true" /><span><strong>Sin alertas destacadas</strong><small>No se superan los umbrales del día.</small></span></div>}
              {comparisonState?.status === 'error' && <Button variant="outline" size="sm" onClick={() => setRetry(value => value + 1)}>Reintentar comparación</Button>}
            </aside>
            </div>
            <div className="market-signals" aria-label="Señales calculadas de la curva">
              <span><small>Valle previsto</small><strong>{minimum ? `${data[minimum.index]?.label} · ${formatEnergyPrice(minimum.value)}` : '—'}</strong></span>
              <span><small>Mayor rampa prevista</small><strong>{ramp ? `${data[ramp.from]?.label}→${data[ramp.to]?.label} · ${formatEnergyPrice(ramp.increase, { sign: true })}` : '—'}</strong></span>
              <span><small>Horas negativas</small><strong>Previstas {negatives.predicted} · reales {negatives.actual}</strong></span>
            </div>
            <p className="chart-caption">Valle, rampa y horas bajo cero se calculan para este día y el modelo de referencia; describen la curva, no atribuyen su causa. La banda es dispersión central entre modelos, no un intervalo de confianza.</p>
          </div>

          <aside className="forecast-rail" aria-label="Indicadores del día">
            <div className="rail-heading"><p className="section-label">Lectura del día</p><h3>{selectedModel || 'Sin modelo'}</h3><span>{format(date, 'dd/MM/yyyy')}</span></div>
            <div className="price-comparison" aria-label="Comparación entre precio medio previsto y real">
              <div className="comparison-price predicted"><span>Precio medio previsto</span><strong>{formatEnergyPrice(comparisonPredicted)}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours} horas comunes` : averages?.predictedHours ? `${averages.predictedHours}/${averages.expectedHours} horas previstas` : 'Sin predicción'}</small></div>
              <div className={`comparison-delta ${averages?.difference == null ? 'pending' : 'neutral'}`}>
                <span>Δ previsto − real</span><strong>{formatEnergyPrice(averages?.difference, { sign: true })}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours}/${averages.expectedHours} comparables` : 'Pendiente de precio real'}</small>
              </div>
              <div className="comparison-price actual"><span>Precio medio real</span><strong>{formatEnergyPrice(comparisonActual)}</strong>
                <small>{averages?.pairedHours ? `${averages.pairedHours} horas comunes` : 'Pendiente de cierre del mercado'}</small></div>
            </div>
            <div className="kpi-stack">
              <MetricCard icon={Database} eyebrow="Cobertura del día"
                value={averages ? `${averages.pairedHours}/${averages.expectedHours}` : '—'}
                detail="Horas con predicción y precio real" />
              <MetricCard icon={ArrowDownRight} eyebrow="Mínimo previsto"
                value={formatEnergyPrice(minimum?.value)} detail={minimum ? data[minimum.index]?.label ?? 'Sin datos' : 'Sin datos'} tone="warm" />
              <MetricCard icon={ArrowUpRight} eyebrow="Máximo previsto"
                value={formatEnergyPrice(max)} detail={peak?.label ?? 'Sin datos'} tone="warm" />
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
                  {MODELS.map(model => <TableCell key={model.key}>{formatEnergyPrice(row.predictions[model.key], { unit: false })}</TableCell>)}
                  <TableCell>{formatEnergyPrice(row.actual, { unit: false })}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          </div>
        </details>

        </> : view === 'evaluation' ? <div className="evaluation-view"><StoredEvaluations onSessionExpired={onSessionExpired} /></div> : view === 'battery' ?
          <div className="battery-view">
            <div className="battery-modes">
              <nav className="battery-mode-switch" aria-label="Vistas de batería">
                <button type="button" aria-pressed={batteryView === 'daily'} onClick={() => setBatteryView('daily')}>Operación diaria</button>
                <button type="button" aria-pressed={batteryView === 'study'} onClick={() => setBatteryView('study')}>Estudio de instalación</button>
              </nav>
              {batteryView === 'daily' ? <>
                <div className="view-datebar">
                  <div><p className="kicker">Operación diaria</p><h2>Plan BESS guardado</h2><p>Consulta la decisión horaria y su resultado económico sin recalcular la estrategia.</p></div>
                  <DateNavigator date={date} days={availableDays} coverageLabel={dayCoverageLabel} ariaLabel="Navegación por fecha BESS" onChange={setDate} />
                </div>
                {currentBattery.status === 'error' && <Button variant="outline" onClick={() => setRetry(value => value + 1)}>Reintentar consulta de batería</Button>}
                <StoredBattery day={day} data={currentBattery.data} status={currentBattery.status} />
              </> : <BatteryStudy />}
            </div>
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
