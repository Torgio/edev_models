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

/**
 * Las ventanas de producción que llegaron antes de que existiera la pantalla
 * guardan algunos supuestos con nombres distintos. No podemos asumir uno
 * concreto, pero sí mostrar un valor corto y estable antes que dos opciones
 * indistinguibles. Las claves de esta lista se traducen sin exponer el JSON.
 */
const EXTRA_FIELDS: Array<{ key: string; label: string; unit?: string }> = [
  { key: 'modo', label: 'modo' },
  { key: 'estrategia', label: 'estrategia' },
  { key: 'politica', label: 'política' },
  { key: 'soc_inicial', label: 'SOC inicial' },
  { key: 'soc_final', label: 'SOC final' },
  { key: 'coste_carga_eur_mwh', label: 'coste de carga', unit: '€/MWh' },
  { key: 'coste_descarga_eur_mwh', label: 'coste de descarga', unit: '€/MWh' },
  { key: 'peajes', label: 'peajes' },
  { key: 'paso_h', label: 'paso', unit: 'h' },
];

const MAX_FIELDS = 3;

const periodLabel = (value: string) => {
  const known: Record<string, string> = {
    prod_30d: 'Producción · últimos 30 días',
    test_2026: 'Prueba · año 2026',
    val_2025: 'Validación · año 2025',
  };
  return known[value] ?? value.replaceAll('_', ' ');
};

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
  const allFields = [...NAMED_FIELDS, ...EXTRA_FIELDS];
  const varying = allFields.filter(field =>
    new Set(assumptions.map(entry => JSON.stringify(entry[field.key]))).size > 1);

  // Con un solo grupo nada «varía»: entonces los campos conocidos sirven igual
  // para describirlo, porque no hay ambigüedad que resolver.
  const fields = (varying.length ? varying : NAMED_FIELDS).slice(0, MAX_FIELDS);

  const labels = groups.map(([key, row]) => {
    // «global» es un detalle interno: si no hay un corte específico, no aporta
    // información a quien escoge el conjunto de evaluación.
    const base = [periodLabel(row.periodo), row.corte && row.corte !== 'global' ? row.corte : null].filter(Boolean).join(' · ');
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

  // Una diferencia puede ser una estructura antigua que no es sensato pintar
  // en un <select> (por ejemplo una lista completa de modelos). En ese caso
  // conservamos ambos conjuntos, pero los nombramos como ejecuciones guardadas
  // distintas: nunca como las opacas «configuración 1/2».
  const repeated = new Map<string, number>();
  return labels.map(item => {
    const total = labels.filter(other => other.label === item.label).length;
    if (total < 2) return item;
    const position = (repeated.get(item.label) ?? 0) + 1;
    repeated.set(item.label, position);
    return {
      ...item,
      label: `${item.label} · ejecución guardada ${position} de ${total}`,
      detail: item.detail ? `${item.detail} · ejecución guardada ${position} de ${total}` : `ejecución guardada ${position} de ${total}`,
    };
  });
}
