'use client';

import { useEffect, useMemo, useState } from 'react';
import { CalendarDays, CheckCircle2, LoaderCircle, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { batterySummary, type BatteryDraft } from '@/lib/battery-draft';
import { studyHorizonEnd } from '@/lib/battery-horizon';

type Task = { tarea: string; estado: 'corriendo' | 'hecho' | 'error'; paso?: string; progreso?: number; escenario?: number; escenarios?: number; run_id?: number; error?: string; resultado?: { run?: { run_id?: number } } };
const API = '/api/battery-study';
const format = (value: number, digits = 2) => value.toLocaleString('es-ES', { maximumFractionDigits: digits });
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value));

type StudyPeriod = { dateFrom: string; dateTo: string; scenarios: number; policy: string };
export function BatteryStudyLaunch({ studyName, battery, period, onPeriodChange, onBusyChange, availableFrom, availableTo, consumptionCode, generationCode, generationIncluded, onBack: back, onComplete }: { studyName: string; battery: BatteryDraft; period: StudyPeriod; onPeriodChange: (period: StudyPeriod) => void; onBusyChange: (busy: boolean) => void; availableFrom: string; availableTo: string; consumptionCode: string; generationCode: string; generationIncluded: boolean; onBack: () => void; onComplete: (runId: number) => void }) {
  const { dateFrom, dateTo, scenarios, policy } = period;
  const setDateFrom = (dateFrom: string) => onPeriodChange({ ...period, dateFrom });
  const setDateTo = (dateTo: string) => onPeriodChange({ ...period, dateTo });
  const setScenarios = (scenarios: number) => onPeriodChange({ ...period, scenarios });
  const setPolicy = (policy: string) => onPeriodChange({ ...period, policy });
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState('');
  const busy = submitting || task?.estado === 'corriendo';
  const onBack = () => { if (!busy) back(); };
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false); }, [busy, onBusyChange]);
  const summary = batterySummary(battery);
  const days = validDate(dateFrom) && validDate(dateTo) ? Math.round((Date.parse(dateTo) - Date.parse(dateFrom)) / 86400000) + 1 : 0;
  useEffect(() => {
    if (!availableFrom || !availableTo || task) return;
    if (!validDate(dateFrom) || dateFrom < availableFrom || dateFrom > availableTo) {
      onPeriodChange({ ...period, dateFrom: availableFrom, dateTo: studyHorizonEnd(availableFrom, 1, availableTo) });
    }
  }, [availableFrom, availableTo, dateFrom, task, period, onPeriodChange]);
  const selectYears = (years: number) => {
    const from = validDate(dateFrom) && dateFrom >= availableFrom && dateFrom <= availableTo ? dateFrom : availableFrom;
    onPeriodChange({ ...period, dateFrom: from, dateTo: studyHorizonEnd(from, years, availableTo) });
  };
  const issues = useMemo(() => {
    const next: string[] = [];
    if (!studyName.trim() || studyName.trim().length > 120) next.push('Escribe un nombre de entre 1 y 120 caracteres en el paso de batería.');
    if (!validDate(dateFrom) || !validDate(dateTo) || dateTo < dateFrom) next.push('El período no es válido.');
    if (days < 2 || days > 7305) next.push('El período debe tener entre 2 días y 20 años.');
    if (!availableFrom || !availableTo) next.push('No se ha podido consultar la cobertura de precios. Vuelve a las curvas y reintenta antes de calcular.');
    if (availableFrom && availableTo && (dateFrom < availableFrom || dateTo > availableTo)) next.push(`El período debe estar dentro de los precios publicados (${availableFrom} → ${availableTo}).`);
    return next;
  }, [availableFrom, availableTo, dateFrom, dateTo, days, studyName]);

  useEffect(() => {
    if (!task || task.estado !== 'corriendo') return;
    const controller = new AbortController();
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API}/estudio/${task.tarea}`, { cache: 'no-store', signal: controller.signal });
        const payload = await response.json() as Task & { detail?: string };
        if (!response.ok) throw new Error(payload.detail || 'No se pudo consultar el progreso.');
        setTask(payload);
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'No se pudo consultar el progreso.'); }
    }, 1000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [task?.tarea, task?.estado]);

  const launch = async () => {
    if (issues.length || busy) return;
    setSubmitting(true);
    onBusyChange(true);
    setError('');
    try {
      const response = await fetch(`${API}/estudio`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        ...battery, studyName: studyName.trim(), consumptionCode, generationCode, generationIncluded, dateFrom, dateTo, scenarios, policy,
      }) });
      const payload = await response.json() as Task & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || 'No se pudo iniciar el estudio.');
      setTask(payload);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'No se pudo iniciar el estudio.'); }
    finally { setSubmitting(false); }
  };
  const completedRun = task?.estado === 'hecho' ? task.run_id ?? task.resultado?.run?.run_id : null;
  return <section className="study-view" aria-labelledby="study-launch-heading">
    <div className="study-heading"><div><p className="kicker">Estudio de instalación · paso 3 de 3</p><h2 id="study-launch-heading">Revisa y calcula</h2><p>Estudia hasta 20 años con los precios publicados. Los perfiles anuales se proyectan al período elegido, aplicando el crecimiento y la degradación configurados.</p></div><Button variant="outline" disabled={busy} onClick={onBack}>← Volver a la batería</Button></div>
    <div className="study-upload-actions" aria-label="Horizonte del estudio">{[1, 5, 10, 20].map(years => <Button key={years} variant="outline" disabled={!!task || busy || !availableFrom || !availableTo} onClick={() => selectYears(years)}>{years} {years === 1 ? 'año' : 'años'}</Button>)}</div>
    <div className="study-launch-grid">
      <article className="study-card"><div className="study-context-panel-title"><CalendarDays aria-hidden="true" /><h3>Período y estrategia</h3></div><div className="study-period-fields">
        <label>Desde<Input type="date" min={availableFrom || undefined} max={availableTo || undefined} value={dateFrom} disabled={!!task} onChange={event => setDateFrom(event.target.value)} /></label>
        <label>Hasta<Input type="date" min={availableFrom || undefined} max={availableTo || undefined} value={dateTo} disabled={!!task} onChange={event => setDateTo(event.target.value)} /></label>
        <label>Trayectorias futuras de precios<NativeSelect value={scenarios} disabled={!!task} onChange={event => setScenarios(Number(event.target.value))}>{[1, 3, 5].map(value => <NativeSelectOption key={value} value={value}>{value} · {value === 1 ? 'rápido' : value === 3 ? 'comparación básica' : 'análisis más robusto'}</NativeSelectOption>)}</NativeSelect></label>
        <label>Política de carga<NativeSelect value={policy} disabled={!!task} onChange={event => setPolicy(event.target.value)}><NativeSelectOption value="libre">Libre</NativeSelectOption><NativeSelectOption value="prefiere_excedente">Prefiere excedente solar</NativeSelectOption><NativeSelectOption value="solo_excedente">Solo excedente solar</NativeSelectOption></NativeSelect></label>
      </div><p className="study-series-note">Cada trayectoria representa una evolución posible de los precios futuros. 1 es rápido, 3 ofrece una comparación básica y 5 da un análisis más robusto.</p><p className="study-series-note">Precios disponibles: {availableFrom || '—'} → {availableTo || '—'}. Puedes ajustar las fechas; los atajos se recortan al último precio disponible.</p><p className="study-series-note">La vida útil se estima al calcular. El VAN utiliza los años completos del período, hasta la vida útil estimada; un horizonte más corto no representa toda la vida de la batería. Los percentiles P10, P50 y P90 solo aparecen cuando el período contiene días simulados.</p></article>
      <article className="study-card study-launch-summary"><div className="study-context-panel-title"><ShieldCheck aria-hidden="true" /><h3>Resumen que se enviará</h3></div><dl>
        <div><dt>Nombre del estudio</dt><dd>{studyName.trim()}</dd></div>
        <div><dt>Consumo</dt><dd>{consumptionCode}</dd></div><div><dt>Generación</dt><dd>{generationIncluded ? generationCode : 'No incluida'}</dd></div><div><dt>Batería</dt><dd>{format(battery.powerKw, 0)} kW / {battery.durationH} h</dd></div><div><dt>Capacidad</dt><dd>{format(summary.capacityMwh)} MWh</dd></div><div><dt>Inversión inicial</dt><dd>{format(summary.totalCostEur, 0)} €</dd></div><div><dt>Período</dt><dd>{days > 0 ? `${days} días` : '—'}</dd></div><div><dt>Escenarios</dt><dd>{scenarios}</dd></div>
      </dl></article>
    </div>
    {issues.length > 0 && <div className="study-notice" role="alert">{issues.join(' ')}</div>}
    {error && <div className="study-notice" role="alert">{error}</div>}
    {task && <article className={`study-task ${task.estado}`} aria-live="polite">{task.estado === 'corriendo' ? <LoaderCircle className="study-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}<div><strong>{task.estado === 'corriendo' ? 'Calculando en el servidor de prueba' : task.estado === 'hecho' ? 'Estudio terminado' : 'El estudio no terminó'}</strong><p>{task.estado === 'error' ? task.error : task.paso || 'Preparando el cálculo'}{task.estado === 'corriendo' && task.escenario ? ` · escenario ${task.escenario}/${task.escenarios}` : ''}</p>{task.estado === 'corriendo' && <progress aria-label="Progreso del optimizador" max={1} value={task.progreso ?? 0} />}</div></article>}
    <div className="study-upload-actions"><Button variant="outline" disabled={task?.estado === 'corriendo'} onClick={onBack}>Atrás</Button>{completedRun
      ? <Button onClick={() => onComplete(completedRun)}>Ver resultado →</Button>
      : <Button disabled={issues.length > 0 || busy || !!task} onClick={() => void launch()}>{submitting ? 'Iniciando cálculo…' : 'Calcular en prueba'}</Button>}</div>
  </section>;
}
