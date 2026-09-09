export type CurveKind = 'consumo' | 'generacion';

export type CurvePreview = {
  days: number;
  hours: number;
  dateFrom: string;
  dateTo: string;
  annualMwh: number;
  peakKw: number;
  meanKw: number[];
  issues: string[];
};

const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

function fields(line: string, separator: string) {
  const out: string[] = [];
  let value = '', quoted = false;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (quoted && line[i + 1] === '"') { value += '"'; i++; }
      else quoted = !quoted;
    } else if (char === separator && !quoted) { out.push(value.trim()); value = ''; }
    else value += char;
  }
  if (quoted) throw new Error('Hay una comilla sin cerrar en el CSV.');
  out.push(value.trim());
  return out;
}

function numberValue(raw: string, separator: string) {
  const normalized = separator === ';' ? raw.replace(/\./g, '').replace(',', '.') : raw;
  const value = Number(normalized);
  return raw.trim() && Number.isFinite(value) ? value : null;
}

function hoursInMadridDay(date: string) {
  const [year, month, day] = date.split('-').map(Number);
  const lastSunday = (monthIndex: number) => {
    const last = new Date(Date.UTC(year, monthIndex + 1, 0));
    return last.getUTCDate() - last.getUTCDay();
  };
  if (month === 3 && day === lastSunday(2)) return 23;
  if (month === 10 && day === lastSunday(9)) return 25;
  return 24;
}

export function previewCurve(text: string, kind: CurveKind): CurvePreview {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(line => line.trim());
  if (lines.length < 25) throw new Error('El fichero tiene muy pocas filas para ser una curva horaria.');
  const separator = (lines[0].match(/;/g)?.length ?? 0) > (lines[0].match(/,/g)?.length ?? 0) ? ';' : ',';
  const header = fields(lines[0], separator).map(value => value.trim().toLowerCase());
  const dateIndex = header.findIndex(value => /^(fecha|date|dia|day)$/.test(value));
  const hourIndex = header.findIndex(value => /^(hora|hour|h)$/.test(value));
  const valueIndex = header.findIndex(value => /(kwh|mwh)/.test(value));
  if (dateIndex < 0 || hourIndex < 0) throw new Error('Se necesitan las columnas fecha y hora.');
  if (valueIndex < 0) throw new Error('La columna de valores debe indicar kWh o MWh en su nombre.');
  const factor = /kwh/.test(header[valueIndex]) ? .001 : 1;
  const dates = new Set<string>();
  const slots = new Set<string>();
  const rawPerDate = new Map<string, number>();
  const normalized = new Map<string, number[]>();
  const sums = Array(24).fill(0) as number[];
  const counts = Array(24).fill(0) as number[];
  let totalMwh = 0, peakMwh = Number.NEGATIVE_INFINITY, negatives = 0;
  for (const line of lines.slice(1)) {
    const row = fields(line, separator);
    const date = row[dateIndex]?.trim();
    const hour = Number(row[hourIndex]);
    const rawValue = row[valueIndex] ?? '';
    const value = numberValue(rawValue, separator);
    if (!validDate(date) || !Number.isInteger(hour) || hour < 1 || hour > 25 || value === null) {
      throw new Error(`Fila horaria no válida: ${line.slice(0, 100)}`);
    }
    const expected = hoursInMadridDay(date);
    if (hour === 25 && expected !== 25) throw new Error(`La hora h25 solo puede aparecer el último domingo de octubre: ${date}.`);
    const slot = `${date}-${hour}`;
    if (slots.has(slot)) throw new Error(`La hora h${hour} del ${date} está duplicada.`);
    slots.add(slot); dates.add(date); rawPerDate.set(date, (rawPerDate.get(date) ?? 0) + 1);
    const mwh = value * factor;
    totalMwh += mwh; peakMwh = Math.max(peakMwh, mwh);
    if (mwh < 0) negatives++;
    const index = expected === 25 ? (hour <= 3 ? hour - 1 : hour - 2)
      : expected === 23 ? (hour <= 2 ? hour - 1 : hour) : hour - 1;
    const normalizedSlot = `${date}-${index}`;
    normalized.set(normalizedSlot, [...normalized.get(normalizedSlot) ?? [], mwh * 1000]);
  }
  const orderedDates = [...dates].sort();
  const issues: string[] = [];
  if (orderedDates.length < 300) issues.push(`Solo contiene ${orderedDates.length} días; hace falta al menos un año completo.`);
  const expectedRaw = orderedDates.reduce((total, date) => total + hoursInMadridDay(date), 0);
  if (slots.size !== expectedRaw || orderedDates.some(date => rawPerDate.get(date) !== hoursInMadridDay(date))) {
    issues.push(`La curva trae ${slots.size} horas y el calendario de Madrid requiere ${expectedRaw} para esos días.`);
  }
  if (totalMwh <= 0) issues.push('La energía total debe ser mayor que cero.');
  if (kind === 'consumo' && negatives) issues.push(`Hay ${negatives} horas con consumo negativo.`);
  for (const [slot, values] of normalized) {
    const index = Number(slot.slice(slot.lastIndexOf('-') + 1));
    sums[index] += values.reduce((sum, value) => sum + value, 0) / values.length;
    counts[index]++;
  }
  const meanKw = sums.map((sum, index) => counts[index] ? sum / counts[index] : 0);
  if (kind === 'generacion') {
    const night = meanKw.slice(0, 5).reduce((sum, value) => sum + value, 0) / 5;
    const mean = meanKw.reduce((sum, value) => sum + value, 0) / 24;
    if (mean > 0 && night / mean > .02) issues.push('La generación fotovoltaica supera el 2 % de su media durante h1–h5; revisa si los ficheros están cruzados.');
  }
  return {
    days: orderedDates.length, hours: slots.size,
    dateFrom: orderedDates[0], dateTo: orderedDates.at(-1) ?? orderedDates[0],
    annualMwh: totalMwh / (orderedDates.length / 365.25), peakKw: peakMwh * 1000,
    meanKw, issues,
  };
}
