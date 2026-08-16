import { dominantDirection } from '../bidi'
import BidiText from './BidiText'

/**
 * One line of the transcript.
 *
 * Direction is decided by `BidiText` (rule in src/bidi.js), not by `dir="auto"`: the base
 * direction follows the *dominant* script, the same rule §6.1 uses to choose the reply voice,
 * and every run of the other script is wrapped in `<bdi>` so punctuation and numbers cannot
 * jump to the wrong end of a code-switched line. That is the finished version of the
 * mixed-direction requirement in product.md §3.6.
 *
 * Bubble alignment is deliberately NOT tied to text direction: the user stays on one side
 * and Sarjy on the other regardless of language, so the conversation does not visually
 * reshuffle when someone code-switches. Text *inside* the bubble is aligned to its own
 * direction, which is the part a bilingual reader actually notices.
 */

const BADGE = {
  ar: 'عربي',
  en: 'English',
  mixed: 'mixed',
}

export default function MessageBubble({ role, text, language, pending }) {
  const isUser = role === 'user'
  const rtl = dominantDirection(text) === 'rtl'

  return (
    <div className={`flex w-full ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`flex max-w-[85ch] flex-col gap-1.5 sm:max-w-[75%] ${
          isUser ? 'items-end' : 'items-start'
        }`}
      >
        <BidiText
          as="div"
          text={text}
          className={[
            'rounded-bubble px-4 py-3 text-[0.975rem] whitespace-pre-wrap',
            rtl ? 'text-right' : 'text-left',
            isUser
              ? 'bg-ink-700/70 text-cream ring-1 ring-ink-600/70'
              : 'bg-amber/10 text-cream ring-1 ring-amber-dim/40',
            pending ? 'opacity-60' : '',
          ].join(' ')}
        />

        {language && (
          <span className="px-1 font-mono text-[0.65rem] tracking-widest text-amber/55 uppercase">
            {BADGE[language] ?? language}
          </span>
        )}
      </div>
    </div>
  )
}
