'use client';

import { useEffect, useMemo, useState } from 'react';
import { CalendarDays, CheckCircle2, LoaderCircle, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { batterySummary, type BatteryDraft } from '@/lib/battery-draft';

type Task = { tarea: string; estado: 'corriendo' | 'hecho' | 'error'; paso?: string; progreso?: number; escenario?: number; escenarios?: number; run_id?: number; error?: string; resultado?: { run?: { run_id?: number } } };
const API = '/api/battery-study';
const format = (value: number, digits = 2) => value.toLocaleString('es-ES', { maximumFractionDigits: digits });
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value));

type StudyPeriod = { dateFrom: string; dateTo: string; scenarios: number; policy: string };
export function BatteryStudyLaunch({ battery, period, onPeriodChange, onBusyChange, availableFrom, availableTo, consumptionCode, generationCode, generationIncluded, onBack: back, onComplete }: { battery: BatteryDraft; period: StudyPeriod; onPeriodChange: (period: StudyPeriod) => void; onBusyChange: (busy: boolean) => void; availableFrom: string; availableTo: string; consumptionCode: string; generationCode: string; generationIncluded: boolean; onBack: () => void; onComplete: (runId: number) => void }) {
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
  const issues = useMemo(() => {
    const next: string[] = [];
    if (!validDate(dateFrom) || !validDate(dateTo) || dateTo < dateFrom) next.push('El período no es válido.');
    if (days < 2 || days > 31) next.push('En la prueba local el período debe tener entre 2 y 31 días.');
    if (availableFrom && availableTo && (dateFrom < availableFrom || dateTo > availableTo)) next.push(`El período debe estar dentro de la curva cargada (${availableFrom} → ${availableTo}).`);
    return next;
  }, [availableFrom, availableTo, dateFrom, dateTo, days]);

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
        ...battery, consumptionCode, generationCode, generationIncluded, dateFrom, dateTo, scenarios, policy,
      }) });
      const payload = await response.json() as Task & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || 'No se pudo iniciar el estudio.');
      setTask(payload);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'No se pudo iniciar el estudio.'); }
    finally { setSubmitting(false); }
  };
  const completedRun = task?.estado === 'hecho' ? task.run_id ?? task.resultado?.run?.run_id : null;
  return <section className="study-view" aria-labelledby="study-launch-heading">
    <div className="study-heading"><div><p className="kicker">Estudio de instalación · paso 3 de 3</p><h2 id="study-launch-heading">Revisa y calcula</h2><p>La prueba está limitada a 2–31 días y 5 escenarios para no competir con los procesos diarios del servidor.</p></div><Button variant="outline" disabled={task?.estado === 'corriendo'} onClick={onBack}>← Volver a la batería</Button></div>
    <div className="study-launch-grid">
      <article className="study-card"><div className="study-context-panel-title"><CalendarDays aria-hidden="true" /><h3>Período y estrategia</h3></div><div className="study-period-fields">
        <label>Desde<Input type="date" min={availableFrom || undefined} max={availableTo || undefined} value={dateFrom} disabled={!!task} onChange={event => setDateFrom(event.target.value)} /></label>
        <label>Hasta<Input type="date" min={availableFrom || undefined} max={availableTo || undefined} value={dateTo} disabled={!!task} onChange={event => setDateTo(event.target.value)} /></label>
        <label>Escenarios<NativeSelect value={scenarios} disabled={!!task} onChange={event => setScenarios(Number(event.target.value))}>{[1, 3, 5].map(value => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}</NativeSelect></label>
        <label>Política de carga<NativeSelect value={policy} disabled={!!task} onChange={event => setPolicy(event.target.value)}><NativeSelectOption value="libre">Libre</NativeSelectOption><NativeSelectOption value="prefiere_excedente">Prefiere excedente solar</NativeSelectOption><NativeSelectOption value="solo_excedente">Solo excedente solar</NativeSelectOption></NativeSelect></label>
      </div><p className="study-series-note">Disponible según la curva de consumo: {availableFrom || '—'} → {availableTo || '—'}. El estudio puede abarcar entre 2 y 31 días.</p></article>
      <article className="study-card study-launch-summary"><div className="study-context-panel-title"><ShieldCheck aria-hidden="true" /><h3>Resumen que se enviará</h3></div><dl>
        <div><dt>Consumo</dt><dd>WEB-CONSUMO</dd></div><div><dt>Generación</dt><dd>{generationIncluded ? 'WEB-GENERACION' : 'No incluida'}</dd></div><div><dt>Batería</dt><dd>{format(battery.powerKw, 0)} kW / {battery.durationH} h</dd></div><div><dt>Capacidad</dt><dd>{format(summary.capacityMwh)} MWh</dd></div><div><dt>Inversión inicial</dt><dd>{format(summary.totalCostEur, 0)} €</dd></div><div><dt>Período</dt><dd>{days > 0 ? `${days} días` : '—'}</dd></div><div><dt>Escenarios</dt><dd>{scenarios}</dd></div>
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
