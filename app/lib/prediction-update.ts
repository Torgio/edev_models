const formatter = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

export function predictionUpdate(timestamp: string | null, current: boolean): string | null {
  if (!current || !timestamp) return null;
  const instant = Date.parse(timestamp);
  return Number.isFinite(instant) ? `${formatter.format(instant)} · Madrid` : null;
}
