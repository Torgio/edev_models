'use client';

import { useState } from 'react';
import { Battery, Info, Settings2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { batteryIssues, batterySummary, DEFAULT_BATTERY, type BatteryDraft } from '@/lib/battery-draft';

const number = (value: number, digits = 2) => value.toLocaleString('es-ES', { maximumFractionDigits: digits });
const money = (value: number) => value.toLocaleString('es-ES', { maximumFractionDigits: 0 });

function NumberField({ label, value, unit, min, max, step, onChange }: { label: string; value: number; unit: string; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return <label className="study-number-field"><span>{label}</span><div><Input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} onChange={event => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))} /><small>{unit}</small></div></label>;
}

export function BatterySetup({ onBack, onConfirm }: { onBack: () => void; onConfirm: (draft: BatteryDraft) => void }) {
  const [draft, setDraft] = useState<BatteryDraft>(DEFAULT_BATTERY);
  const [ready, setReady] = useState(false);
  const set = <K extends keyof BatteryDraft>(key: K, value: BatteryDraft[K]) => { setReady(false); setDraft(current => ({ ...current, [key]: value })); };
  const summary = batterySummary(draft), issues = batteryIssues(draft);
  return <section className="study-view" aria-labelledby="battery-setup-heading">
    <div className="study-heading"><div><p className="kicker">Estudio de instalación · paso 2 de 3</p><h2 id="battery-setup-heading">Define la batería</h2><p>Introduce los datos de la ficha técnica. La potencia se expresa en kW y el precio por MWh instalado.</p></div><Button variant="outline" onClick={onBack}>← Volver a las curvas</Button></div>
    <article className="study-card study-battery-form">
      <div className="study-battery-basics">
        <NumberField label="Potencia" value={draft.powerKw} unit="kW" min={5} max={100000} step={5} onChange={value => set('powerKw', value)} />
        <fieldset className="study-duration"><legend>Duración</legend><div>{[1, 2, 3, 4, 6, 8].map(hours => <Button type="button" key={hours} variant={draft.durationH === hours ? 'default' : 'outline'} onClick={() => set('durationH', hours)}>{hours} h</Button>)}</div></fieldset>
        <NumberField label="Precio de la batería" value={draft.capexEurMwh} unit="€/MWh" min={20000} max={2000000} step={10000} onChange={value => set('capexEurMwh', value)} />
      </div>
      <div className="study-battery-summary"><div className="study-battery-summary-title"><Battery aria-hidden="true" /><span>Con estos datos</span></div><dl>
        <div><dt>Capacidad instalada</dt><dd>{number(summary.capacityMwh)} <small>MWh</small></dd></div>
        <div><dt>Energía útil</dt><dd>{number(summary.usableMwh)} <small>MWh</small></dd></div>
        <div><dt>Coste total</dt><dd>{money(summary.totalCostEur)} <small>€</small></dd></div>
        <div><dt>Coste teórico de ciclo</dt><dd>{summary.cycleCostEurMwh == null ? '—' : number(summary.cycleCostEurMwh, 1)} <small>€/MWh</small></dd></div>
      </dl></div>
      <p className="study-battery-note"><Info aria-hidden="true" /><span><strong>El precio no es el coste total.</strong> Se multiplica por la capacidad instalada: {number(summary.capacityMwh)} MWh × {money(draft.capexEurMwh)} €/MWh.</span></p>
      <details className="study-battery-advanced"><summary><Settings2 aria-hidden="true" />Opciones avanzadas de la ficha técnica</summary><div className="study-advanced-grid">
        <NumberField label="Rendimiento ida y vuelta" value={draft.efficiencyPct} unit="%" min={70} max={99} step={1} onChange={value => set('efficiencyPct', value)} />
        <NumberField label="Ciclos de vida" value={draft.cycles} unit="ciclos" min={1000} max={15000} step={500} onChange={value => set('cycles', value)} />
        <NumberField label="Carga mínima (SoC)" value={draft.socMinPct} unit="%" min={0} max={30} step={1} onChange={value => set('socMinPct', value)} />
        <NumberField label="Carga máxima (SoC)" value={draft.socMaxPct} unit="%" min={70} max={100} step={1} onChange={value => set('socMaxPct', value)} />
        <NumberField label="Carga máxima" value={draft.chargeMaxPct} unit="% nominal" min={10} max={100} step={5} onChange={value => set('chargeMaxPct', value)} />
        <NumberField label="Descarga máxima" value={draft.dischargeMaxPct} unit="% nominal" min={10} max={100} step={5} onChange={value => set('dischargeMaxPct', value)} />
        <NumberField label="Mínimo técnico" value={draft.minimumPowerPct} unit="% nominal" min={0} max={50} step={5} onChange={value => set('minimumPowerPct', value)} />
      </div>{draft.minimumPowerPct > 0 && <p className="study-notice">Un mínimo técnico mayor que cero convierte el cálculo en un problema entero mixto y puede multiplicar el tiempo de ejecución.</p>}</details>
    </article>
    {issues.length > 0 && <div className="study-notice" role="alert"><strong>Revisa la ficha:</strong><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
    {ready && <div className="study-upload-success" role="status">Configuración lista: {number(draft.powerKw, 0)} kW / {draft.durationH} h, {number(summary.capacityMwh)} MWh y {money(summary.totalCostEur)} € de inversión inicial.</div>}
    <div className="study-upload-actions"><Button variant="outline" onClick={onBack}>Atrás</Button>{ready
      ? <Button onClick={() => onConfirm(draft)}>Continuar al período →</Button>
      : <Button disabled={issues.length > 0} onClick={() => setReady(true)}>Confirmar batería</Button>}</div>
  </section>;
}
