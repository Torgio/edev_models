'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, FileUp, Sun } from 'lucide-react';
import { Line, LineChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { previewCurve, type CurveKind, type CurvePreview } from '@/lib/battery-curve-preview';
import { BatterySetup } from '@/components/battery-setup';
import { BatteryStudyLaunch } from '@/components/battery-study-launch';
import { DEFAULT_BATTERY, batteryIssues, type BatteryDraft } from '@/lib/battery-draft';

type Selected = { file: File; preview: CurvePreview };
type UploadResult = { tipo: CurveKind; ok: boolean; code: string; problemas: string[]; curva: CurvePreview & { anual_mwh: number; pico_kw: number } };
type SavedInstallation = { code: string; nombre?: string; anual_mwh?: number; tecnologia?: string; mwp?: number; desde?: string | null; hasta?: string | null; horas?: number | null };
type PriceCurveStatus = { desde: string; hasta: string; escenarios: number; generada: string; ultimo_dato_real: string };
const metric = (value: number, digits = 1) => value.toLocaleString('es-ES', { maximumFractionDigits: digits });
const firstStudyWindow = (from: string, to: string) => {
  const next = new Date(`${from}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  const candidate = next.toISOString().slice(0, 10);
  return candidate <= to ? candidate : to;
};

function CurveInput({ kind, selected, onChange }: { kind: CurveKind; selected: Selected | null; onChange: (value: Selected | null, error?: string) => void }) {
  const [error, setError] = useState('');
  const choose = async (file?: File) => {
    setError('');
    if (!file) return;
    if (!/\.(csv|txt)$/i.test(file.name)) { const message = 'En esta prueba usa CSV o TXT; Excel se conectará en la siguiente etapa.'; setError(message); onChange(null, message); return; }
    if (file.size > 5 * 1024 * 1024) { const message = 'El fichero supera el límite de 5 MB.'; setError(message); onChange(null, message); return; }
    try { onChange({ file, preview: previewCurve(await file.text(), kind) }); }
    catch (cause) { const message = cause instanceof Error ? cause.message : 'No se pudo leer el fichero.'; setError(message); onChange(null, message); }
  };
  const title = kind === 'consumo' ? 'Curva de consumo' : 'Curva de generación';
  return <section className={`study-upload-box ${selected?.preview.issues.length ? 'has-error' : selected ? 'is-ready' : ''}`}>
    <div className="study-upload-icon">{kind === 'consumo' ? <FileUp aria-hidden="true" /> : <Sun aria-hidden="true" />}</div>
    <div><h4>{title}</h4><p>{kind === 'consumo' ? 'Obligatoria · un año horario' : 'Opcional · fotovoltaica'}</p></div>
    <label className="study-file-button">Elegir CSV<Input type="file" accept=".csv,.txt,text/csv,text/plain" onChange={event => void choose(event.target.files?.[0])} /></label>
    {error && <p className="study-upload-error"><AlertCircle aria-hidden="true" />{error}</p>}
    {selected && <div className="study-upload-summary">
      <p><strong>{selected.file.name}</strong><span>{metric(selected.preview.days, 0)} días · {metric(selected.preview.hours, 0)} horas</span></p>
      <dl><div><dt>Período</dt><dd>{selected.preview.dateFrom} → {selected.preview.dateTo}</dd></div><div><dt>Energía anual</dt><dd>{metric(selected.preview.annualMwh)} MWh</dd></div><div><dt>Punta</dt><dd>{metric(selected.preview.peakKw)} kW</dd></div></dl>
      {selected.preview.issues.length ? <ul>{selected.preview.issues.map(issue => <li key={issue}>{issue}</li>)}</ul> : <p className="study-upload-ok"><CheckCircle2 aria-hidden="true" />Lista para guardar</p>}
    </div>}
  </section>;
}

export function BatteryCurveUpload({ onCancel, onComplete }: { onCancel: () => void; onComplete: (runId: number) => void }) {
  const [step, setStep] = useState<'curves' | 'battery' | 'launch'>('curves');
  const [battery, setBattery] = useState<BatteryDraft>(DEFAULT_BATTERY);
  const [period, setPeriod] = useState({ dateFrom: '', dateTo: '', scenarios: 1, policy: 'libre' });
  const [busy, setBusy] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [consumption, setConsumption] = useState<Selected | null>(null);
  const [generation, setGeneration] = useState<Selected | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [saved, setSaved] = useState<UploadResult[]>([]);
  const [savedInstallations, setSavedInstallations] = useState<{ consumo: SavedInstallation[]; generacion: SavedInstallation[] }>({ consumo: [], generacion: [] });
  const [savedConsumptionCode, setSavedConsumptionCode] = useState('');
  const [savedGenerationCode, setSavedGenerationCode] = useState('');
  const [priceCurve, setPriceCurve] = useState<PriceCurveStatus | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/battery-study/instalaciones', { cache: 'no-store', signal: controller.signal }).then(async response => {
      if (!response.ok) return;
      const data = await response.json() as { consumo?: SavedInstallation[]; generacion?: SavedInstallation[] };
      if (!controller.signal.aborted) setSavedInstallations({ consumo: Array.isArray(data.consumo) ? data.consumo : [], generacion: Array.isArray(data.generacion) ? data.generacion : [] });
    }).catch(() => undefined);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/battery-study/estado', { cache: 'no-store', signal: controller.signal }).then(async response => {
      if (!response.ok) return;
      const data = await response.json() as PriceCurveStatus;
      if (!controller.signal.aborted && data.desde && data.hasta) setPriceCurve(data);
    }).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const rangeMismatch = Boolean(consumption && generation && (consumption.preview.dateFrom !== generation.preview.dateFrom || consumption.preview.dateTo !== generation.preview.dateTo));
  const ready = consumption && !consumption.preview.issues.length && (!generation || !generation.preview.issues.length) && !rangeMismatch;
  const uploaded = saved.length > 0 && saved.every(item => item.ok);
  const stored = uploaded || Boolean(savedConsumptionCode);
  const chart = useMemo(() => Array.from({ length: 24 }, (_, index) => ({ hour: `h${index + 1}`, consumption: consumption?.preview.meanKw[index] ?? null, generation: generation?.preview.meanKw[index] ?? null })), [consumption, generation]);
  const submit = async () => {
    if (!ready || uploading) return;
    setUploading(true); setMessage(''); setSaved([]);
    const body = new FormData(); body.set('consumo', consumption.file);
    if (generation) body.set('generacion', generation.file);
    try {
      const response = await fetch('/api/battery-study/curvas', { method: 'POST', body });
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string' ? payload.detail : 'No se pudieron guardar las curvas.');
      if (!Array.isArray(payload) || !payload.some(item => item?.tipo === 'consumo') || (generation && !payload.some(item => item?.tipo === 'generacion')) || payload.some(item => !item || typeof item !== 'object' || typeof item.ok !== 'boolean')) throw new Error('El servidor devolvió una respuesta incompleta.');
      const results = payload as UploadResult[]; setSaved(results);
      setSavedConsumptionCode(results.find(item => item.tipo === 'consumo' && item.ok)?.code ?? '');
      setSavedGenerationCode(results.find(item => item.tipo === 'generacion' && item.ok)?.code ?? '');
      setMessage(results.every(item => item.ok) ? 'Curvas guardadas en el entorno de prueba.' : 'El servidor rechazó al menos una curva; revisa los avisos.');
      if (results.every(item => item.ok)) setStep('battery');
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : 'No se pudieron guardar las curvas.'); }
    finally { setUploading(false); }
  };
  const canReview = stored && reviewed && batteryIssues(battery).length === 0;
  return <>
    <nav className="study-steps" aria-label="Pasos del estudio"><ol>{(['curves', 'battery', 'launch'] as const).map((item, index) => <li key={item}><button type="button" aria-current={step === item ? 'step' : undefined} disabled={busy || uploading || (item === 'battery' && !stored) || (item === 'launch' && !canReview)} onClick={() => setStep(item)}><span>{index + 1}</span>{['Curvas', 'Batería', 'Calcular'][index]}</button></li>)}</ol></nav>
    {step === 'battery' ? <BatterySetup draft={battery} onChange={setBattery} onBack={() => setStep('curves')} onConfirm={() => { setReviewed(true); setStep('launch'); }} />
    : step === 'launch' ? <BatteryStudyLaunch battery={battery} period={period} onPeriodChange={setPeriod} onBusyChange={setBusy} availableFrom={consumption?.preview.dateFrom ?? ''} availableTo={consumption?.preview.dateTo ?? ''} consumptionCode={savedConsumptionCode || 'WEB-CONSUMO'} generationCode={savedGenerationCode || 'WEB-GENERACION'} generationIncluded={generation !== null || Boolean(savedGenerationCode)} onBack={() => setStep('battery')} onComplete={onComplete} />
    : <section className="study-view" aria-labelledby="curve-upload-heading">
    <div className="study-heading"><div><p className="kicker">Estudio de instalación · paso 1 de 3</p><h2 id="curve-upload-heading">Sube tus curvas</h2><p>Comprueba primero la forma de consumo y generación. El servidor volverá a validarlas antes de guardarlas.</p>{priceCurve && <p className="study-series-note">Curva de precios publicada: {priceCurve.desde} → {priceCurve.hasta} · {priceCurve.escenarios} escenarios.</p>}</div><Button variant="outline" onClick={onCancel}>Ver resultados guardados</Button></div>
    {savedInstallations.consumo.length > 0 && <div className="study-saved-curves"><strong>También puedes usar una curva guardada</strong><div className="study-saved-curves-grid"><label>Consumo<NativeSelect value={savedConsumptionCode} onChange={event => { const item = savedInstallations.consumo.find(candidate => candidate.code === event.target.value); setSavedConsumptionCode(event.target.value); setConsumption(null); setSaved([]); setMessage(''); if (item?.desde && item.hasta) setPeriod(current => ({ ...current, dateFrom: item.desde!, dateTo: firstStudyWindow(item.desde!, item.hasta!) })); }}>{<NativeSelectOption value="">Subir un fichero nuevo</NativeSelectOption>}{savedInstallations.consumo.map(item => <NativeSelectOption key={item.code} value={item.code}>{item.nombre || item.code}</NativeSelectOption>)}</NativeSelect></label><label>Generación opcional<NativeSelect value={savedGenerationCode} onChange={event => { setSavedGenerationCode(event.target.value); setGeneration(null); setSaved([]); setMessage(''); }}>{<NativeSelectOption value="">Sin generación guardada</NativeSelectOption>}{savedInstallations.generacion.map(item => <NativeSelectOption key={item.code} value={item.code}>{item.nombre || item.code}</NativeSelectOption>)}</NativeSelect></label></div></div>}
    <div className="study-upload-grid"><CurveInput kind="consumo" selected={consumption} onChange={value => { setConsumption(value); setSavedConsumptionCode(''); setSaved([]); setMessage(''); if (value) setPeriod(current => ({ ...current, dateFrom: value.preview.dateFrom, dateTo: firstStudyWindow(value.preview.dateFrom, value.preview.dateTo) })); }} /><CurveInput kind="generacion" selected={generation} onChange={value => { setGeneration(value); setSavedGenerationCode(''); setSaved([]); setMessage(''); }} /></div>
    {rangeMismatch && <div className="study-notice" role="alert">Las curvas deben cubrir el mismo período. Revisa las fechas de consumo y generación antes de guardarlas.</div>}
    {consumption && <article className="study-card"><div className="study-chart-heading"><div><h3>El día medio de tus curvas</h3><p>Consumo y generación medios por hora. Una fotovoltaica debe caer a cero durante la noche.</p></div></div><div className="study-profile-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0}><LineChart data={chart} margin={{ top: 18, right: 18, left: 8, bottom: 8 }}><CartesianGrid vertical={false} stroke="#e1e8e4" /><XAxis dataKey="hour" interval={2} /><YAxis width={62} unit=" kW" /><Tooltip formatter={value => `${metric(Number(value))} kW`} /><Legend /><Line dataKey="consumption" name="Consumo" stroke="#173f35" strokeWidth={2.5} dot={false} /><Line dataKey="generation" name="Generación" stroke="#e6a229" strokeWidth={2.5} dot={false} /></LineChart></ResponsiveContainer></div></article>}
    {message && <div className={saved.every(item => item.ok) && saved.length ? 'study-upload-success' : 'study-notice'} role="status">{message}{saved.map(item => <p key={item.tipo}><strong>{item.tipo === 'consumo' ? 'Consumo' : 'Generación'}:</strong> {item.ok ? `${metric(item.curva.anual_mwh)} MWh/año · guardada como ${item.code}` : item.problemas.join(' ')}</p>)}</div>}
    <div className="study-upload-actions"><Button variant="outline" disabled={uploading} onClick={onCancel}>Cancelar</Button>{stored
      ? <Button onClick={() => setStep('battery')}>Continuar con la batería →</Button>
      : <Button disabled={!ready || uploading} onClick={() => void submit()}>{uploading ? 'Guardando curvas…' : 'Guardar y continuar →'}</Button>}</div>
  </section>}</>;
}
