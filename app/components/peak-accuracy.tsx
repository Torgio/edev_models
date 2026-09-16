'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';

type PeakResult = {
  model: string; hits: number; evaluated_days: number; excluded_days: number;
  window_days: number; start_date: string; end_date: string;
};
const displayDate = (date: string) => date.split('-').reverse().join('/');

export function PeakAccuracy({ day, model }: { day: string; model: string; onSessionExpired: () => void }) {
  const [state, setState] = useState<{ day: string; model: string; data?: PeakResult; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    if (!model) return () => controller.abort();
    fetch(`/api/dashboard/peak-accuracy?model=${encodeURIComponent(model)}&source=production&days=30&end_date=${encodeURIComponent(day)}`, {
      signal: controller.signal, cache: 'no-store',
    }).then(async response => {
      if (!response.ok) throw new Error('No se pudo consultar el acierto de pico.');
      return await response.json() as PeakResult;
    }).then(data => {
      if (!controller.signal.aborted) setState({ day, model, data });
    }).catch(() => {
      if (!controller.signal.aborted) setState({ day, model, error: 'Acierto de pico no disponible. No se muestran cifras de demostración.' });
    });
    return () => controller.abort();
  }, [day, model]);
  const current = state?.day === day && state.model === model ? state : null;
  const result = current?.data;
  return <div>
    <strong>Acierto del pico · {model || 'Sin modelo'}</strong>
    {!model ? <p>Sin modelo disponible para evaluar.</p> : !current ? <p role="status">Consultando los últimos 30 días cerrados…</p>
      : current.error ? <p role="status">{current.error}</p>
      : result && <>
        <p className="peak-counter">{result.evaluated_days ? `${result.hits} de ${result.evaluated_days} días evaluables` : 'Sin días completos para evaluar'}</p>
        <p className="peak-method-summary">Cuenta como acierto si la hora más cara prevista queda a una hora o menos de la real.</p>
        <p>{displayDate(result.start_date)}–{displayDate(result.end_date)} · {result.window_days} días · {result.excluded_days} excluidos.</p>
        <Dialog>
          <DialogTrigger render={<Button className="peak-definition-trigger" variant="outline" size="sm" />}>Ver criterios de cálculo</DialogTrigger>
          <DialogContent className="peak-definition-dialog">
            <DialogHeader><DialogTitle>Cómo se calcula el acierto del pico</DialogTitle><DialogDescription>Una medida de precisión horaria; no mide el ingreso de una batería.</DialogDescription></DialogHeader>
            <div className="peak-definition-copy">
              <p><strong>Días incluidos.</strong> Solo días cerrados con precios reales y predicciones de producción en todas sus horas (23, 24 o 25). El período termina en el día seleccionado o ayer, si eliges hoy o una fecha futura.</p>
              <p><strong>Qué cuenta como acierto.</strong> Si hay máximos previstos empatados, se toma el primero. Se acepta cualquiera de los máximos reales a una hora de distancia como máximo, medida por tiempo transcurrido.</p>
              <p><strong>Alcance.</strong> Se calcula sobre las predicciones guardadas actualmente; no audita sus revisiones históricas. Acertar el pico no garantiza un mayor ingreso de batería.</p>
            </div>
          </DialogContent>
        </Dialog>
      </>}
  </div>;
}
