'use client';

import { useEffect, useState } from 'react';
import './battery-study.css';
import { Battery, CalendarDays, Factory, Info } from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { BatteryCurveUpload } from '@/components/battery-curve-upload';
import { metric } from '@/lib/stored-evaluations';
import { nominalDayOffset, studyCoverage, studyInputs, studyPoints, toKilo, type StudyDispatch, type StudyResult } from '@/lib/battery-study';

const API = '/api/battery-study';
const dateText = (value: string | null) => value ? new Date(value).toLocaleString('es-ES', { timeZone: 'Europe/Madrid' }) : 'Sin fecha guardada';
const chartNumber = (value: number) => value.toLocaleString('es-ES', { maximumFractionDigits: 0 });
const dayCount = (value: number | null | undefined) => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? chartNumber(value) : '—';
const nominalDateText = (value: string) => value.split('-').reverse().join('/');
const scenarioCount = (value: number) => `${value} escenario${value === 1 ? '' : 's'}`;
const originText = (value: string) => value === 'historico' ? 'Histórico' : value === 'simulado' ? 'Simulado' : value;

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API}/${path}`, { cache: 'no-store', signal });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null;
    throw new Error(typeof detail === 'string' ? detail : 'No se pudo consultar el estudio.');
  }
  return payload as T;
}

export function BatteryStudy() {
  const [creating, setCreating] = useState(false);
  return creating ? <BatteryCurveUpload onCancel={() => setCreating(false)} /> : <SavedBatteryStudy onNew={() => setCreating(true)} />;
}

function SavedBatteryStudy({ onNew }: { onNew: () => void }) {
  const [ids, setIds] = useState<number[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [result, setResult] = useState<StudyResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [start, setStart] = useState('');
  const [span, setSpan] = useState(7);
  const [dispatch, setDispatch] = useState<StudyDispatch | null>(null);
  const [dispatchError, setDispatchError] = useState('');
  const [dispatchLoading, setDispatchLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setError(''); setLoading(true);
    read<{ runs: number[] }>('opciones', controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setIds(data.runs);
      setRunId(previous => previous && data.runs.includes(previous) ? previous : data.runs[0] ?? null);
      if (!data.runs.length) { setLoading(false); setError('No hay estudios habilitados para esta prueba local.'); }
    }).catch(e => { if (!controller.signal.aborted) { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    if (runId === null) return;
    const controller = new AbortController();
    setResult(null); setDispatch(null); setStart(''); setError(''); setLoading(true);
    read<StudyResult>(`resultado/${runId}`, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      if (data.run?.run_id !== runId || !Array.isArray(data.anual)) throw new Error('Respuesta de estudio incompleta.');
      setResult(data); setLoading(false);
      const years = [...data.anual].sort((a, b) => a.ano - b.ano);
      const initial = years.find(row => row.dias >= 365) ?? years[0];
      const savedInputs = studyInputs(data);
      if (savedInputs) setStart(savedInputs.period.date_from);
      else if (initial) setStart(`${initial.ano}-01-01`);
    }).catch(e => { if (!controller.signal.aborted) { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [runId, reload]);

  useEffect(() => {
    setDispatch(null); setDispatchError(''); setDispatchLoading(false);
    if (!result || result.run.run_id !== runId || !start || !/^\d{4}-\d{2}-\d{2}$/.test(start)) return;
    const controller = new AbortController();
    setDispatch(null); setDispatchError(''); setDispatchLoading(true);
    const query = new URLSearchParams({ desde: start, hasta: nominalDayOffset(start, span - 1) });
    read<StudyDispatch>(`despacho/${runId}?${query}`, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      studyPoints(data); // Validate array alignment before allowing any graph to render.
      setDispatch(data); setDispatchLoading(false);
    }).catch(e => { if (!controller.signal.aborted) { setDispatchError(e.message); setDispatchLoading(false); } });
    return () => controller.abort();
  }, [result, runId, start, span, reload]);

  const points = dispatch ? studyPoints(dispatch) : [];
  const run = result?.run;
  const inputs = result ? studyInputs(result) : null;
  const annual = [...result?.anual ?? []].sort((a, b) => a.ano - b.ano);
  return <section className="study-view" aria-labelledby="study-heading">
    <div className="study-heading">
      <div><p className="kicker">Estudio de instalación · prueba local</p><h2 id="study-heading">Una batería, a lo largo del tiempo</h2>
        <p>Consulta una ejecución guardada y explora su operación horaria.</p></div>
      <div className="study-controls">
        <Button onClick={onNew}>Nuevo estudio</Button>
        {ids.length > 0 && <label>Estudio guardado<NativeSelect value={runId ?? ''} onChange={e => setRunId(Number(e.target.value))}>
          {ids.map(id => <NativeSelectOption key={id} value={id}>Estudio {id}</NativeSelectOption>)}
        </NativeSelect></label>}
        <Button variant="outline" onClick={() => setReload(n => n + 1)}>Actualizar</Button>
      </div>
    </div>
    {error ? <div className="study-notice" role="alert">{error}</div> : loading ? <div className="study-empty" role="status">Consultando el estudio guardado…</div> : run && <>
      <article className="study-card study-context" aria-labelledby="study-context-heading">
        <header className="study-context-header">
          <div><h3 id="study-context-heading">{inputs?.case.name || 'Contexto del estudio'}</h3><p className="study-context-intro">{inputs?.case.code && `${inputs.case.code} · `}Ejecutado el {dateText(run.run_at)}</p></div>
          <div className="study-context-ids"><span>Caso <strong>{run.case_id}</strong></span><span>Ejecución <strong>{run.run_id}</strong></span></div>
        </header>
        <div className="study-context-panels">
          <section className="study-context-panel study-context-period" aria-labelledby="study-period-heading">
            <div className="study-context-panel-title"><CalendarDays aria-hidden="true" /><h4 id="study-period-heading">Período evaluado</h4></div>
            <dl className="study-context-counts">
              <div><dd>{dayCount(run.days_historical)}</dd><dt>días históricos</dt></div>
              <div><dd>{dayCount(run.days_simulated)}</dd><dt>días simulados</dt></div>
            </dl>
            <p className="study-context-years"><span>Años con resultados</span>{studyCoverage(annual)}</p>
            <p className="study-context-note">{inputs ? `${nominalDateText(inputs.period.date_from)} — ${nominalDateText(inputs.period.date_to)} · período original` : 'Puede incluir años parciales. Fechas exactas no disponibles.'}</p>
          </section>
          <section className="study-context-panel" aria-labelledby="study-installation-heading">
            <div className="study-context-panel-title"><Factory aria-hidden="true" /><h4 id="study-installation-heading">Instalación</h4></div>
            <span className={inputs ? 'study-context-saved' : 'study-context-unavailable'}>{inputs ? 'Datos guardados al ejecutar' : 'Parámetros originales no disponibles'}</span>
            {inputs && <p className="study-context-note">Consumo: {inputs.consumption?.name || (inputs.consumption === null ? 'No incluido' : 'Nombre no disponible')}<br />Generación: {inputs.generation?.name || (inputs.generation === null ? 'No incluida' : 'Nombre no disponible')}</p>}
            <dl className="study-context-fields"><div><dt>Consumo anual</dt><dd>{inputs?.consumption === null ? 'No aplica' : <>{metric(inputs?.consumption?.annual_mwh)} <small>MWh/año</small></>}</dd></div><div><dt>{inputs?.generation && inputs.generation.technology !== 'fv' ? 'Potencia de generación' : 'Potencia solar'}</dt><dd>{inputs?.generation === null ? 'No aplica' : <>{metric(toKilo(inputs?.generation?.capacity_mwp))} <small>{inputs?.generation && inputs.generation.technology !== 'fv' ? 'kW' : 'kWp'}</small></>}</dd></div></dl>
          </section>
          <section className="study-context-panel" aria-labelledby="study-battery-heading">
            <div className="study-context-panel-title"><Battery aria-hidden="true" /><h4 id="study-battery-heading">Batería</h4></div>
            <span className={inputs ? 'study-context-saved' : 'study-context-unavailable'}>{inputs ? 'Datos guardados al ejecutar' : 'Parámetros originales no disponibles'}</span>
            {inputs && <p className="study-context-note">{inputs.battery.name || inputs.battery.code} · {metric(inputs.battery.duration_h)} h</p>}
            <dl className="study-context-fields"><div><dt>Potencia</dt><dd>{metric(toKilo(inputs?.battery.power_mw))} <small>kW</small></dd></div><div><dt>Capacidad</dt><dd>{metric(toKilo(inputs?.battery.capacity_mwh))} <small>kWh</small></dd></div></dl>
          </section>
        </div>
        <p className="study-context-footnote"><Info aria-hidden="true" /><span>Solo se muestran datos de esta ejecución. Los parámetros ausentes no se sustituyen por la configuración actual ni se deducen de los gráficos.</span></p>
      </article>
      <div className="study-notice"><strong>Resultado económico pendiente de revisión.</strong> El VAN se muestra tal como quedó guardado. Estamos revisando cómo se contabiliza el desgaste junto con la inversión inicial.</div>
      <div className="study-metrics">
        <article><span>VAN mediano guardado</span><strong>{metric(run.npv_p50, ' €')}</strong><small>P10 {metric(run.npv_p10, ' €')} · P90 {metric(run.npv_p90, ' €')}</small></article>
        <article><span>Escenarios con VAN positivo</span><strong>{metric(run.npv_positive_pct, ' %')}</strong><small>De {run.n_scenarios} escenarios evaluados; no es una garantía.</small></article>
        <article><span>Ahorro acumulado del período</span><strong>{metric(run.savings_vs_no_batt, ' €')}</strong><small>{run.savings_vs_no_batt == null ? 'Sin ahorro frente a una instalación sin batería guardado.' : 'Comparación guardada frente a no tener batería.'}</small></article>
        <article><span>Uso diario</span><strong>{metric(run.cycles_per_day)} <em>ciclos/día</em></strong><small>Vida estimada guardada: {metric(run.life_years, ' años')}</small></article>
      </div>
      <article className="study-card">
        <div className="study-chart-heading"><div><h3>Valor económico por año</h3><p>Promedio guardado de los escenarios. Los años parciales conservan su cobertura.</p></div></div>
        {annual.length ? <div className="study-annual-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={annual} margin={{ top: 12, right: 16, left: 16, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="#e1e8e4" /><XAxis dataKey="ano" tickLine={false} />
            <YAxis tickFormatter={chartNumber} width={70} tickLine={false} /><ReferenceLine y={0} stroke="#82908b" />
            <Tooltip formatter={v => [metric(Number(v), ' €'), 'Valor anual guardado']} labelFormatter={year => {
              const row = annual.find(a => a.ano === Number(year)); return `${year} · ${row?.dias ?? '—'} días · ${row?.origen ?? ''}`;
            }} /><Bar dataKey="margen" fill="#258160" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer></div> : <p>No hay resultados anuales guardados.</p>}
      </article>
      <article className="study-card">
        <div className="study-chart-heading"><div><h3>Consumo, generación y batería</h3><p>Potencia media horaria en kW. La carga y la descarga se muestran como valores positivos.</p></div>
          <div className="study-controls"><label>Desde<Input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
            <label>Tramo<NativeSelect value={span} onChange={e => setSpan(Number(e.target.value))}>
              <NativeSelectOption value={1}>Un día</NativeSelectOption><NativeSelectOption value={7}>Una semana</NativeSelectOption>
              <NativeSelectOption value={30}>30 días</NativeSelectOption>
            </NativeSelect></label>
            <Button variant="outline" disabled={!start} aria-label="Tramo anterior" onClick={() => setStart(nominalDayOffset(start, -span))}>←</Button>
            <Button variant="outline" disabled={!start} aria-label="Tramo siguiente" onClick={() => setStart(nominalDayOffset(start, span))}>→</Button>
          </div>
        </div>
        {dispatchLoading ? <div className="study-empty" role="status">Consultando el despacho horario…</div> : dispatchError ? <div className="study-notice" role="alert">{dispatchError} Puedes elegir otra fecha.</div> : points.length ? <>
          <p className="study-series-note">Trayectoria guardada · escenario {dispatch?.escenario} · {points.length} horas. Esta trayectoria no representa necesariamente la mediana del estudio.</p>
          <div className="study-dispatch-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <ComposedChart data={points} margin={{ top: 15, right: 16, left: 0, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="#e1e8e4" /><XAxis dataKey="label" minTickGap={45} tick={{ fontSize: 12 }} /><YAxis width={60} />
              <Tooltip formatter={v => metric(Number(v), ' kW')} /><Legend />
              <Bar dataKey="charge" name="Carga" fill="#43a99f" /><Bar dataKey="discharge" name="Descarga" fill="#e58b45" />
              <Line type="linear" dataKey="load" name="Consumo" stroke="#173f35" strokeWidth={2} dot={false} connectNulls={false} />
              <Line type="linear" dataKey="generation" name="Generación" stroke="#d19a3a" strokeWidth={2} dot={false} connectNulls={false} />
            </ComposedChart>
          </ResponsiveContainer></div>
          <div className="study-secondary-charts">
            <div><h4>Estado de carga · kWh</h4><ResponsiveContainer width="100%" height={190} minWidth={0}>
              <AreaChart data={points} margin={{ left: 0, right: 16 }}><XAxis dataKey="label" minTickGap={60} tick={{ fontSize: 12 }} /><YAxis width={60} />
                <Tooltip formatter={v => metric(Number(v), ' kWh')} /><Area type="stepAfter" dataKey="soc" name="Energía almacenada" stroke="#173f35" fill="#43a99f" fillOpacity={.18} connectNulls={false} />
              </AreaChart></ResponsiveContainer></div>
            <div><h4>Precio utilizado · €/MWh</h4><ResponsiveContainer width="100%" height={190} minWidth={0}>
              <AreaChart data={points} margin={{ left: 0, right: 16 }}><XAxis dataKey="label" minTickGap={60} tick={{ fontSize: 12 }} /><YAxis width={60} />
                <Tooltip formatter={v => metric(Number(v), ' €/MWh')} /><Area type="linear" dataKey="price" name="Precio del escenario" stroke="#7b8ee8" fill="#7b8ee8" fillOpacity={.1} connectNulls={false} />
              </AreaChart></ResponsiveContainer></div>
          </div>
          <p className="study-series-note">h1–h24 son horas del calendario nominal del estudio. El precio pertenece a esta ejecución; no se sustituye por la curva publicada hoy.</p>
        </> : <div className="study-empty">No hay despacho guardado para este tramo.</div>}
      </article>
      <details className="study-card study-details"><summary>Procedencia y valores guardados</summary>
        <p>Estudio {run.run_id} · caso {run.case_id} · ejecutado {dateText(run.run_at)}.</p>
        <p>{run.curve_generated_at
          ? <>Curva futura utilizada: {dateText(run.curve_generated_at)} · {scenarioCount(run.n_scenarios)}.</>
          : run.days_simulated === 0
            ? <>Curva futura: no aplica; todo el período es histórico · {scenarioCount(run.n_scenarios)}.</>
            : <>Curva futura: fecha no guardada · {scenarioCount(run.n_scenarios)}.</>}</p>
        {run.curve_matrix_hash && <p>Huella de matriz: <code>{run.curve_matrix_hash}</code></p>}
        {run.notes && <p>Notas guardadas: {run.notes}</p>}
        <p>Los indicadores y el despacho se leen del estudio. En pantalla solo se convierten unidades y se formatean valores. {inputs ? `Parámetros conservados el ${dateText(inputs.captured_at)} (versión ${inputs.schema_version}).` : 'Los parámetros originales de la instalación no están incluidos en esta respuesta.'}</p>
        <div className="table-scroll"><table><caption>Resultados anuales de la ejecución</caption><thead><tr>{['Año', 'Origen', 'Días', 'Valor medio €', 'P10 €', 'P50 €', 'P90 €'].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead>
          <tbody>{annual.map(row => <tr key={row.ano}><td>{row.ano}</td><td>{originText(row.origen)}</td><td>{row.dias}</td><td>{metric(row.margen)}</td><td>{metric(row.p10)}</td><td>{metric(row.p50)}</td><td>{metric(row.p90)}</td></tr>)}</tbody>
        </table></div>
      </details>
    </>}
  </section>;
}
