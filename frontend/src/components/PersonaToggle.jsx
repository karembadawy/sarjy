/**
 * The persona switch, 🇪🇬 / 🇸🇦 (product.md §7).
 *
 * A persona is a system-prompt style guide and a voice — nothing else — so switching one
 * mid-call needs no reconnect and no reset. The backend reads the row at the start of every
 * turn, which means the change lands on the next thing Sarjy says and the conversation
 * carries on around it.
 */

const PERSONAS = [
  { key: 'egyptian', flag: '🇪🇬', label: 'مصري' },
  { key: 'gulf', flag: '🇸🇦', label: 'خليجي' },
]

export default function PersonaToggle({ persona, onChange, disabled }) {
  return (
    <div
      role="group"
      aria-label="Sarjy's dialect · لهجة سرجي"
      className="flex items-center gap-1 rounded-full border border-ink-700 bg-ink-900/60 p-1"
    >
      {PERSONAS.map(({ key, flag, label }) => {
        const active = persona === key
        return (
          <button
            key={key}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => onChange(key)}
            title={label}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm transition-colors disabled:opacity-40 ${
              active ? 'bg-amber/15 text-amber' : 'text-muted hover:text-cream'
            }`}
          >
            <span aria-hidden className="text-base leading-none">
              {flag}
            </span>
            <span className="hidden sm:inline">{label}</span>
          </button>
        )
      })}
    </div>
  )
}
