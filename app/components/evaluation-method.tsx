const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const text = (value: unknown) => typeof value === 'string' && value.trim() ? value : 'No registrado';
const reasons: Record<string, string> = {
  cobertura_insuficiente: 'Cobertura insuficiente',
  sin_datos_ultimo_dia: 'Sin datos del último día',
};
const descriptions: Record<string, string> = {
  'MILP cronologico; sin carga/descarga simultanea; SOC final fijado': 'Optimización cronológica: sin cargar y descargar a la vez y con el nivel de carga final fijado.',
  'solo coste_descarga_eur_mwh; sin peajes ni O&M': 'Solo el coste por MWh descargado; no incluye peajes ni operación y mantenimiento.',
};
const describe = (value: unknown) => typeof value === 'string' ? descriptions[value] ?? text(value) : text(value);

export function EvaluationMethod({ simulator }: { simulator: Record<string, unknown> | null | undefined }) {
  if (!simulator) return <p>Sin supuestos registrados; comparabilidad económica no verificada.</p>;
  const cohort = record(simulator.cohorte);
  const included = Array.isArray(cohort?.incluidos) ? cohort.incluidos : null;
  const excluded = Array.isArray(cohort?.excluidos) ? cohort.excluidos : null;
  return <section className="evaluation-method" aria-label="Cómo se ha calculado">
    <h4>Cómo se ha calculado</h4>
    <dl>
      <div><dt>Evaluación</dt><dd>{simulator.tipo === 'simulacion_fuera_de_muestra' ? 'Simulación fuera de muestra' : text(simulator.tipo)}</dd></div>
      <div><dt>Reglas de operación</dt><dd>{describe(simulator.regla)}</dd></div>
      <div><dt>Costes considerados</dt><dd>{describe(simulator.costes)}</dd></div>
      <div><dt>Resolución temporal</dt><dd>{typeof simulator.paso_h === 'number' && simulator.paso_h > 0 ? `${simulator.paso_h.toLocaleString('es-ES')} h por período` : 'No registrada'}</dd></div>
    </dl>
    {cohort && <div className="evaluation-method-cohort">
      <p><strong>Modelos y semillas comparados:</strong> {included === null ? 'Inclusiones no registradas' : `${included.length} incluidos`}{excluded !== null && ` · ${excluded.length} excluidos`}.</p>
      {included !== null && included.length > 0 && <details><summary>Ver modelos incluidos</summary><ul>{included.map((value, index) => {
        const row = record(value);
        return <li key={index}>{text(row?.model)}{typeof row?.seed === 'number' && row.seed !== -1 ? ` · semilla ${row.seed}` : ''}{typeof row?.periodos_disponibles === 'number' ? ` · ${row.periodos_disponibles.toLocaleString('es-ES')} períodos disponibles` : ''}</li>;
      })}</ul></details>}
      {excluded !== null && excluded.length > 0 && <ul>{excluded.map((value, index) => {
        const row = record(value);
        const causes = Array.isArray(row?.motivos) ? row.motivos.filter((reason): reason is string => typeof reason === 'string') : [];
        return <li key={index}><strong>{text(row?.model)}</strong>{typeof row?.seed === 'number' && row.seed !== -1 ? ` · semilla ${row.seed}` : ''}: {causes.length ? causes.map(reason => reasons[reason] ?? reason.replaceAll('_', ' ')).join('; ') : 'Motivo no registrado'}.</li>;
      })}</ul>}
    </div>}
    <details className="evaluation-method-raw"><summary>Ver datos técnicos (JSON)</summary><pre>{JSON.stringify(simulator, null, 2)}</pre></details>
  </section>;
}
