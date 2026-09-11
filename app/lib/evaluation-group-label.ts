import { type Evaluation } from './stored-evaluations';

/**
 * «Configuración 1 / 2 / 3» no dice nada a quien no escribió el código, pero el
 * simulador guarda una veintena de campos —hashes de muestra, marcas UTC,
 * cohortes enteras— que tampoco sirven como nombre.
 *
 * Esta función nombra un grupo con los pocos campos que un lector reconoce:
 * potencia, duración, rendimiento y ciclos. Solo menciona los que de verdad
 * cambian entre grupos, y nunca más de tres.
 */

/** Campos que identifican una configuración, en el orden en que se leen. */
const NAMED_FIELDS: Array<{ key: string; label: string; unit?: string }> = [
  { key: 'potencia_mw', label: '', unit: 'MW' },
  { key: 'duracion_h', label: '', unit: 'h' },
  { key: 'rendimiento', label: 'rend.' },
  { key: 'eficiencia', label: 'rend.' },
  { key: 'ciclos_max', label: 'ciclos' },
  { key: 'ciclos_dia', label: 'ciclos/día' },
  { key: 'horizonte', label: '' },
  { key: 'version', label: '' },
];

const MAX_FIELDS = 3;

/** Descarta lo que no se puede leer de un vistazo: objetos, hashes, fechas, textos largos. */
const readable = (value: unknown): value is string | number =>
  (typeof value === 'number' && Number.isFinite(value)) ||
  (typeof value === 'string' && value.length > 0 && value.length <= 24 && !/^\d{4}-\d{2}-\d{2}/.test(value) && !/^[0-9a-f]{16,}$/i.test(value));

const show = (field: { label: string; unit?: string }, value: string | number) => {
  const text = typeof value === 'number' ? value.toLocaleString('es-ES', { maximumFractionDigits: 2 }) : value;
  if (field.unit) return `${text} ${field.unit}`;
  return field.label ? `${field.label} ${text}` : text;
};

export type GroupLabel = { key: string; label: string; detail: string | null };

export function evaluationGroupLabels(groups: Array<[string, Evaluation]>): GroupLabel[] {
  const assumptions = groups.map(([, row]) => row.simulador ?? {});
  const varying = NAMED_FIELDS.filter(field =>
    new Set(assumptions.map(entry => JSON.stringify(entry[field.key]))).size > 1);

  // Con un solo grupo nada «varía»: entonces los campos conocidos sirven igual
  // para describirlo, porque no hay ambigüedad que resolver.
  const fields = (varying.length ? varying : NAMED_FIELDS).slice(0, MAX_FIELDS);

  return groups.map(([key, row]) => {
    const base = [row.periodo, row.corte].filter(Boolean).join(' · ');
    const detail = fields
      .map(field => {
        const value = (row.simulador ?? {})[field.key];
        return readable(value) ? show(field, value) : null;
      })
      .filter((entry): entry is string => entry !== null);

    if (detail.length) return { key, label: `${base} · ${detail.join(' · ')}`, detail: detail.join(' · ') };
    // Sin supuestos legibles no hay nada que nombrar: dilo, no lo numeres.
    return { key, label: row.simulador ? base : `${base} · sin supuestos registrados`, detail: null };
  });
}
