import { dominantDirection, segments } from '../bidi'

/**
 * One line of possibly-mixed Arabic/English text, laid out the way a bilingual reader
 * expects. The rule lives in src/bidi.js; this is the two lines of markup that apply it.
 *
 * `<bdi>` is the whole trick: one tag, no CSS, and it means exactly "resolve this run's
 * direction on its own and treat the result as a single neutral object". That is what stops
 * a question mark at the end of a code-switched sentence from jumping to the far end of the
 * line.
 *
 * @param {{text: string, className?: string, as?: string}} props
 */
export default function BidiText({ text, className, as: Tag = 'span' }) {
  const direction = dominantDirection(text)
  return (
    <Tag dir={direction} className={className}>
      {segments(text, direction).map((part, index) =>
        part.isolate ? <bdi key={index}>{part.text}</bdi> : part.text,
      )}
    </Tag>
  )
}
