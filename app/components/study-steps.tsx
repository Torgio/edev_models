export function StudySteps({ step }: { step: 1 | 2 | 3 }) {
  return <ol className="study-steps" aria-label="Pasos del estudio">
    {['Curvas', 'Batería', 'Revisión'].map((label, index) => <li key={label} aria-current={step === index + 1 ? 'step' : undefined} className={index + 1 < step ? 'complete' : ''}>
      <span aria-hidden="true">{index + 1 < step ? '✓' : index + 1}</span>{label}
    </li>)}
  </ol>;
}
