'use client';
import { useState } from 'react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Database, TrendingUp, Zap } from 'lucide-react';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { metric, numeric } from '@/lib/stored-evaluations';
import { batteryModels, storedPlanSummary } from '@/lib/stored-battery';
import type { BatteryPayload } from '@/lib/battery-types';
const hour = new Intl.DateTimeFormat('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const timestamp = new Intl.DateTimeFormat('es-ES', { timeZone: 'Europe/Madrid', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const planDate = new Intl.DateTimeFormat('es-ES', { timeZone: 'Europe/Madrid', weekday: 'long', day: 'numeric', month: 'long' });
const signed = (value: number | null) => value == null ? '—' : `${value > 0 ? '+' : ''}${metric(value, ' €')}`;
const label = (key: string) => ({ potencia_mw: 'Potencia', capacidad_mwh: 'Capacidad', eficiencia: 'Eficiencia', ciclos_dia: 'Ciclos/día', horizonte: 'Horizonte' }[key] ?? key.replaceAll('_', ' '));
const assumptionValue = (key: string, value: unknown) => {
  if (key === 'eficiencia' && numeric(value)) return `${metric(value * 100)} %`;
  if (key === 'potencia_mw' && numeric(value)) return `${metric(value)} MW`;
  if (key === 'capacidad_mwh' && numeric(value)) return `${metric(value)} MWh`;
  return String(value);
};

export function StoredBattery({ day, data, status }: { day: string; data: BatteryPayload | null; status: 'loading' | 'ready' | 'error' }) {
  const [chosenModel, setChosenModel] = useState('');
  if (status === 'loading') return <section className="battery-section battery-loading" aria-label="Optimización BESS" aria-busy="true">Consultando optimización BESS…</section>;
  if (status === 'error') return <section className="battery-section battery-empty" aria-label="Optimización BESS">Optimización BESS no disponible. No se sustituye por una simulación.</section>;
  if (!data || data.date !== day) return null;
  if (!data.results.length && !data.plan.length) return null;

  const models = batteryModels(data.plan, data.results);
  const model = models.includes(chosenModel) ? chosenModel : models.includes('ensemble') ? 'ensemble' : models[0];
  const plan = data.plan.filter(row => row.model === model).sort((a, b) => Date.parse(a.datetime) - Date.parse(b.datetime));
  const result = data.results.find(row => row.model === model);
  const assumptions = plan[0]?.simulador ?? result?.simulador;
  const points = plan.map(row => ({
    datetime: row.datetime,
    hour: hour.format(new Date(row.datetime)),
    soc: row.soc_mwh,
    action: row.carga_mw > 0 ? 'charge' : row.descarga_mw > 0 ? 'discharge' : 'idle',
    power: row.carga_mw > 0 ? row.carga_mw : row.descarga_mw,
    income: row.ingreso_eur,
  }));
  const summary = storedPlanSummary(plan);
  const chargeHours = plan.filter(row => row.carga_mw > 0).map(row => hour.format(new Date(row.datetime)));
  const dischargeHours = plan.filter(row => row.descarga_mw > 0).map(row => hour.format(new Date(row.datetime)));
  const updated = plan[0]?.updated_at ?? result?.calculado_en;
  const comparison = result ? [
    { label: 'Modelo', value: result.ingreso_eur, color: '#43a99f' },
    { label: 'Oráculo evaluador', value: result.ingreso_oraculo_eur, color: '#e58b45' },
    { label: 'Naive', value: result.ingreso_naive_eur, color: '#82908b' },
  ] : [];
  const capacity = assumptions && numeric(assumptions.capacidad_mwh) ? assumptions.capacidad_mwh : summary.maxSoc;

  return <section className="battery-section" id="bateria" aria-labelledby="battery-title">
    <div className="battery-header">
      <div><p className="section-label">Segundo paso · datos guardados</p><h2 id="battery-title">Optimización de la batería</h2>
        <p>Del precio previsto a un plan operativo de carga y descarga. La API solo lee el plan y el resultado almacenados.</p></div>
      <label>Modelo
        <NativeSelect value={model} onChange={event => setChosenModel(event.target.value)}>
          {models.map(value => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}
        </NativeSelect>
      </label>
    </div>

    {plan.length > 0 ? <article className="battery-decision-card" aria-label="Resumen del plan BESS guardado">
      <div className="decision-copy">
        <span>Plan guardado · {planDate.format(new Date(`${day}T12:00:00`))}</span>
        <h3>{chargeHours.length ? `Cargar ${chargeHours.join(', ')}` : 'Sin horas de carga guardadas'}</h3>
        <p>{dischargeHours.length ? `Descargar ${dischargeHours.join(', ')}` : 'Sin horas de descarga guardadas'}</p>
        <small>Modelo {model} · {summary.observations} tramos · estrategia y supuestos almacenados</small>
      </div>
      <div className="decision-value"><span>Ingreso previsto</span><strong>{signed(summary.income)}</strong><small>{summary.income == null ? 'Plan económico incompleto' : 'Suma de los importes horarios guardados'}</small></div>
    </article> : <div className="battery-decision-empty">No hay un plan horario guardado para esta fecha y modelo.</div>}

    <div className="battery-kpis">
      <article><TrendingUp /><span>Ingreso realizado</span><strong>{signed(result?.ingreso_eur ?? null)}</strong><small>{result ? 'Liquidado con precio real' : 'Pendiente de precio real'}</small></article>
      <article><Zap /><span>Captura sobre el oráculo</span><strong>{metric(result?.captura_pct, ' %')}</strong><small>Oráculo definido por el evaluador</small></article>
      <article><Database /><span>Estado de carga máximo</span><strong>{metric(summary.maxSoc, ' MWh')}</strong><small>{updated ? `Actualizado ${timestamp.format(new Date(updated))}` : 'Sin actualización'}</small></article>
    </div>

    <div className="battery-grid">
      <article className="battery-chart-card">
        <div className="visual-heading"><div><p className="section-label">Plan horario · {model}</p><h3>Qué hace la batería</h3></div></div>
        {points.length ? <>
          <div className="battery-timeline-scroll"><div className="battery-timeline" aria-label="Línea temporal del plan BESS">
            {points.map(point => <div key={point.datetime} className={`timeline-hour ${point.action}`}
              title={`${point.hour} · ${point.action === 'charge' ? 'Carga' : point.action === 'discharge' ? 'Descarga' : 'Espera'} · ${metric(point.power)} MW`}>
              <small>{point.hour.slice(0,2)}</small><span>{point.action === 'charge' ? '↓' : point.action === 'discharge' ? '↑' : '·'}</span>
              <strong>{point.action === 'idle' ? '' : `${metric(point.power)} MW`}</strong>
            </div>)}</div></div>
          <div className="soc-heading"><span>Estado de carga</span><strong>0–{metric(capacity, ' MWh')}</strong></div>
          <div className="soc-chart">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={190}>
              <AreaChart data={points} margin={{ top: 12, right: 16, bottom: 2, left: 2 }}>
                <CartesianGrid vertical={false} stroke="#e1e8e4" />
                <XAxis dataKey="hour" interval={2} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: '#667770' }} />
                <YAxis domain={[0, capacity ?? 'auto']} tickLine={false} axisLine={false} width={52} tick={{ fontSize: 10, fill: '#667770' }} />
                <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #d8e0dc' }}
                  formatter={value => [`${metric(Number(value))} MWh`, 'Estado de carga']} />
                <Area type="stepAfter" dataKey="soc" name="Estado de carga" stroke="#173f35" strokeWidth={2.7} fill="#43a99f" fillOpacity={.15} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="battery-actions"><span><i className="charge" />Carga: <strong>{chargeHours.join(', ') || '—'}</strong></span>
            <span><i className="discharge" />Descarga: <strong>{dischargeHours.join(', ') || '—'}</strong></span></div>
        </> : <div className="battery-chart-empty">No hay plan horario guardado para esta fecha.</div>}
      </article>

      <aside className="battery-result-card">
        <div className="visual-heading"><div><p className="section-label">Flujo económico guardado</p><h3>De coste a ingreso</h3></div></div>
        {summary.income != null ? <div className="economic-waterfall">
          <div className="flow-step cost"><span>Coste de carga</span><strong>{signed(summary.chargeCost)}</strong></div><i>+</i>
          <div className="flow-step sale"><span>Venta de energía</span><strong>{signed(summary.dischargeRevenue)}</strong></div><i>=</i>
          <div className="flow-step net"><span>Ingreso previsto</span><strong>{signed(summary.income)}</strong></div>
        </div> : <div className="battery-chart-empty compact">Sin ingreso completo guardado para el plan.</div>}
        {result ? <>
          <div className="result-comparison">{comparison.map(item => <div key={item.label}><i style={{ background: item.color }} /><span>{item.label}</span><strong>{signed(item.value)}</strong></div>)}</div>
          <dl className="battery-result-list">
            <div><dt>Captura sobre el oráculo</dt><dd>{metric(result.captura_pct, ' %')}</dd></div>
            <div><dt>Ciclos registrados</dt><dd>{metric(result.ciclos)}</dd></div>
          </dl>
          <p>El oráculo es el definido por el evaluador y sus supuestos; no garantiza el máximo técnicamente operable.</p>
        </> : <p className="pending-result">El plan es futuro: la comparación realizada aparecerá cuando exista precio real.</p>}
      </aside>
    </div>

    {assumptions && <div className="assumption-chips" aria-label="Supuestos registrados">{Object.entries(assumptions).filter(([key]) => key !== 'regla').map(([key, value]) =>
      <span key={key}><small>{label(key)}</small><strong>{assumptionValue(key, value)}</strong></span>)}</div>}

    <details className="battery-audit"><summary>Ver detalle horario y definición guardada</summary>
      {assumptions && <p><strong>Regla:</strong> {String(assumptions.regla ?? 'Sin registrar')}</p>}
      {plan.length > 0 && <div className="table-scroll"><Table>
        <TableHeader><TableRow>{['Hora', 'Carga MW', 'Descarga MW', 'SOC MWh', 'Ingreso €'].map(value => <TableHead key={value}>{value}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{plan.map(row => <TableRow key={row.datetime}><TableCell>{hour.format(new Date(row.datetime))}</TableCell><TableCell>{metric(row.carga_mw)}</TableCell><TableCell>{metric(row.descarga_mw)}</TableCell><TableCell>{metric(row.soc_mwh)}</TableCell><TableCell>{signed(row.ingreso_eur)}</TableCell></TableRow>)}</TableBody>
      </Table></div>}
      <p>Lectura directa de bess_plan y bess_result. No se recalculan horas, ingresos, captura ni supuestos en la aplicación.</p>
    </details>
  </section>;
}
