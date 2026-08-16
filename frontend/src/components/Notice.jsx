import { useEffect, useState } from 'react'

import BidiText from './BidiText'

/**
 * The designed state for "something ran out" (product.md §4).
 *
 * Three shapes, because three different things are true and pretending otherwise is the
 * failure this replaces:
 *
 *   retryable  a per-minute limit. It really will clear, so we count down out loud and ask
 *              again by ourselves — the person already said their sentence once.
 *   transient  the server is already fixing it (a Deepgram channel reconnecting). Nothing to
 *              do but explain the pause; it clears itself.
 *   neither    a spent daily allowance. There is no honest spinner for this, so it says so
 *              plainly and stops. A countdown here would be a lie with an animation on it.
 *
 * Bilingual, Arabic first, and `dir="auto"` on each line so a mixed sentence lays out
 * correctly (§3.6). Amber, not red: nothing here is broken, something is rationed.
 */
export default function Notice({ notice, onRetry, onDismiss }) {
  const [remaining, setRemaining] = useState(0)

  const seconds = Math.ceil(notice?.retry_after_s ?? 0)
  const countingDown = Boolean(notice && (notice.retryable || notice.transient) && seconds > 0)

  useEffect(() => {
    if (!countingDown) return undefined
    setRemaining(seconds)
    const tick = setInterval(() => setRemaining((left) => Math.max(0, left - 1)), 1000)
    const done = setTimeout(() => {
      clearInterval(tick)
      // A transient notice has nothing to ask for — it just stops being true.
      if (notice.retryable) onRetry?.()
      else onDismiss?.()
    }, seconds * 1000)
    return () => {
      clearInterval(tick)
      clearTimeout(done)
    }
    // `notice` is replaced wholesale on every error frame, so identity is the right trigger.
  }, [notice, countingDown, seconds, onRetry, onDismiss])

  if (!notice) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className="space-y-1.5 rounded-bubble border border-amber-dim/50 bg-amber/8 px-4 py-3"
    >
      {/* The Arabic line names providers in Latin ("خلصت حصة النهاردة من Gemini المجانية"),
          which is the exact case `<bdi>` exists for — see src/bidi.jsx. */}
      <BidiText as="p" text={notice.message_ar} className="text-[0.95rem] text-amber-soft" />
      <BidiText as="p" text={notice.message_en} className="text-sm text-cream/60" />

      <div className="flex items-center gap-3 pt-1 text-xs text-muted">
        {countingDown ? (
          <span className="flex items-center gap-2">
            <span
              aria-hidden
              className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber"
            />
            {notice.retryable
              ? `بجرب تاني بعد ${remaining} · retrying in ${remaining}s`
              : `${remaining}s`}
          </span>
        ) : (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-full border border-ink-600 px-3 py-1 transition-colors hover:border-muted hover:text-cream"
          >
            تمام · Got it
          </button>
        )}

        {notice.speak_again && (
          <span>بصوت المتصفح · read in your browser&apos;s voice</span>
        )}
      </div>
    </div>
  )
}
