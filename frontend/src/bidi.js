/**
 * Mixed-direction text, worked out in one place (product.md §3.6). No JSX here on purpose —
 * this is the rule, `components/BidiText.jsx` is the rendering of it, and the rule is the
 * part worth being able to read and check on its own.
 *
 * `dir="auto"` — what the transcript used through Phase 4 — gets a *pure* Arabic or English
 * line right and a code-switched one wrong, in two different ways.
 *
 * **1. It picks the base direction from the first strong character.** "Book لي ميعاد بكرة
 * بعد العصر" is an Arabic sentence with one English word at the front, and `auto` lays the
 * whole line out left-to-right because of that one word. The base direction should follow
 * the same rule the reply voice follows — the dominant script (§6.1) — so a sentence is laid
 * out as what it mostly is. That is `dominantDirection()`, and it is deliberately the same
 * 50% rule as `language.dominant_language()` on the server.
 *
 * **2. Neutral characters between two scripts attach to the wrong side.** This is the one
 * that produces the classic broken screenshot. In "الميتنج ده online ولا في الأوفيس؟" the
 * final "؟" is neutral, and the bidi algorithm resolves neutrals from their *neighbours*:
 * with an English run sitting next to Arabic, a trailing question mark, a full stop, or a
 * time like "5:30" can end up at the opposite end of the line from the words it belongs to.
 * The Unicode fix is isolation — an isolated run is resolved on its own and then treated as
 * one neutral object by everything around it, so nothing leaks in either direction.
 *
 * So: base direction from the dominant script, every run of the *other* script isolated.
 * Runs keep the digits and punctuation that sit *inside* them ("Sidi Gaber at 9:15") and
 * never swallow the space that ends them, which is what keeps the isolation minimal.
 */

// The same Arabic blocks the backend's language.py counts, so one rule decides direction on
// both sides of the socket. Written as escapes rather than literal characters: a range of
// invisible Arabic code points pasted into a character class is unreviewable in a diff.
const ARABIC =
  '\u0600-\u06FF' + // Arabic
  '\u0750-\u077F' + // Arabic Supplement
  '\u0870-\u08FF' + // Arabic Extended-A and -B
  '\uFB50-\uFDFF' + // Presentation Forms-A
  '\uFE70-\uFEFF' //  Presentation Forms-B

// Latin including the accented ranges, so an accented word is one run and not three.
const LATIN = 'A-Za-z\u00C0-\u024F'

// Characters with no direction of their own. Allowed *between* two strong characters of the
// same script, so "9:15", "doctor's" and "Sidi Gaber" stay one run — and never at its edges,
// which is what stops a run from swallowing the space or full stop that follows it.
const NEUTRAL = "0-9 \\t'\u2019`\\-\u2013\u2014.,:;!?&/()\\[\\]%+"

const arabicRun = new RegExp(`[${ARABIC}]+(?:[${NEUTRAL}]+[${ARABIC}]+)*`, 'g')
const latinRun = new RegExp(`[${LATIN}]+(?:[${NEUTRAL}]+[${LATIN}]+)*`, 'g')
const anyArabic = new RegExp(`[${ARABIC}]`)
const anyLatin = new RegExp(`[${LATIN}]`)

/** Share of strong characters that are Arabic. Mirrors language.arabic_ratio on the server. */
export function arabicRatio(text) {
  const letters = [...(text ?? '')].filter((c) => anyArabic.test(c) || anyLatin.test(c))
  if (!letters.length) return 0
  return letters.filter((c) => anyArabic.test(c)).length / letters.length
}

/** 'rtl' or 'ltr' — which way this line as a whole should be laid out. */
export function dominantDirection(text) {
  return arabicRatio(text) >= 0.5 ? 'rtl' : 'ltr'
}

/**
 * Cut `text` into segments, marking which ones must be isolated.
 *
 * Only runs of the direction *opposite* to the base are isolated: wrapping same-direction
 * runs as well would add markup that changes nothing, and isolating the whole string would
 * defeat the point.
 *
 * @returns {{text: string, isolate: boolean}[]}
 */
export function segments(text, direction) {
  const source = text ?? ''
  const pattern = direction === 'rtl' ? latinRun : arabicRun
  pattern.lastIndex = 0

  const parts = []
  let cursor = 0
  let match
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > cursor) {
      parts.push({ text: source.slice(cursor, match.index), isolate: false })
    }
    parts.push({ text: match[0], isolate: true })
    cursor = match.index + match[0].length
  }
  if (cursor < source.length) parts.push({ text: source.slice(cursor), isolate: false })
  return parts
}
