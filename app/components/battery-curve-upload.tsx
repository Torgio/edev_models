'use client';

import { useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, FileUp, Sun } from 'lucide-react';
import { Line, LineChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { previewCurve, type CurveKind, type CurvePreview } from '@/lib/battery-curve-preview';

type Selected = { file: File; preview: CurvePreview };
type UploadResult = { tipo: CurveKind; ok: boolean; code: string; problemas: string[]; curva: CurvePreview & { anual_mwh: number; pico_kw: number } };
const metric = (value: number, digits = 1) => value.toLocaleString('es-ES', { maximumFractionDigits: digits });

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

export function BatteryCurveUpload({ onCancel }: { onCancel: () => void }) {
  const [consumption, setConsumption] = useState<Selected | null>(null);
  const [generation, setGeneration] = useState<Selected | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [saved, setSaved] = useState<UploadResult[]>([]);
  const ready = consumption && !consumption.preview.issues.length && (!generation || !generation.preview.issues.length);
  const chart = useMemo(() => Array.from({ length: 24 }, (_, index) => ({ hour: `h${index + 1}`, consumption: consumption?.preview.meanKw[index] ?? null, generation: generation?.preview.meanKw[index] ?? null })), [consumption, generation]);
  const submit = async () => {
    if (!ready) return;
    setUploading(true); setMessage(''); setSaved([]);
    const body = new FormData(); body.set('consumo', consumption.file);
    if (generation) body.set('generacion', generation.file);
    try {
      const response = await fetch('/api/battery-study/curvas', { method: 'POST', body });
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string' ? payload.detail : 'No se pudieron guardar las curvas.');
      if (!Array.isArray(payload) || payload.some(item => !item || typeof item !== 'object' || !('ok' in item))) throw new Error('El servidor devolvió una respuesta incompleta.');
      const results = payload as UploadResult[]; setSaved(results);
      setMessage(results.every(item => item.ok) ? 'Curvas guardadas en el entorno de prueba.' : 'El servidor rechazó al menos una curva; revisa los avisos.');
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : 'No se pudieron guardar las curvas.'); }
    finally { setUploading(false); }
  };
  return <section className="study-view" aria-labelledby="curve-upload-heading">
    <div className="study-heading"><div><p className="kicker">Estudio de instalación · paso 1 de 3</p><h2 id="curve-upload-heading">Sube tus curvas</h2><p>Comprueba primero la forma de consumo y generación. El servidor volverá a validarlas antes de guardarlas.</p></div><Button variant="outline" onClick={onCancel}>Ver resultados guardados</Button></div>
    <div className="study-upload-grid"><CurveInput kind="consumo" selected={consumption} onChange={value => { setConsumption(value); setSaved([]); setMessage(''); }} /><CurveInput kind="generacion" selected={generation} onChange={value => { setGeneration(value); setSaved([]); setMessage(''); }} /></div>
    {consumption && <article className="study-card"><div className="study-chart-heading"><div><h3>El día medio de tus curvas</h3><p>Consumo y generación medios por hora. Una fotovoltaica debe caer a cero durante la noche.</p></div></div><div className="study-profile-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0}><LineChart data={chart} margin={{ top: 18, right: 18, left: 8, bottom: 8 }}><CartesianGrid vertical={false} stroke="#e1e8e4" /><XAxis dataKey="hour" interval={2} /><YAxis width={62} unit=" kW" /><Tooltip formatter={value => `${metric(Number(value))} kW`} /><Legend /><Line dataKey="consumption" name="Consumo" stroke="#173f35" strokeWidth={2.5} dot={false} /><Line dataKey="generation" name="Generación" stroke="#e6a229" strokeWidth={2.5} dot={false} /></LineChart></ResponsiveContainer></div></article>}
    {message && <div className={saved.every(item => item.ok) && saved.length ? 'study-upload-success' : 'study-notice'} role="status">{message}{saved.map(item => <p key={item.tipo}><strong>{item.tipo === 'consumo' ? 'Consumo' : 'Generación'}:</strong> {item.ok ? `${metric(item.curva.anual_mwh)} MWh/año · guardada como ${item.code}` : item.problemas.join(' ')}</p>)}</div>}
    <div className="study-upload-actions"><Button variant="outline" onClick={onCancel}>Cancelar</Button><Button disabled={!ready || uploading} onClick={() => void submit()}>{uploading ? 'Validando en el servidor…' : 'Guardar curvas en prueba'}</Button></div>
  </section>;
}
