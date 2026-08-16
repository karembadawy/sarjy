/**
 * The last link of the D-005 provider chain: the browser's own speech synthesiser.
 *
 * Gemini TTS is metered per model per day (D-038), and voice.py already walks a chain of
 * models when one runs out. When the *whole* chain is spent, the server can only send the
 * reply as text and say so — and until Phase 5 that is exactly what happened: Sarjy went
 * silent mid-conversation with an apology on screen.
 *
 * `speechSynthesis` is free, offline, installed on every browser we support, and nobody's
 * quota. It is a worse voice than Gemini's and a much worse one than ElevenLabs', which is
 * precisely why it is the fallback and not the default — but a slightly robotic sentence is
 * a far better answer than a call that has stopped talking to you.
 *
 * It is never used for anything else: the server is the voice of this product, and this file
 * only ever runs on an error frame that carries `speak_again`.
 */

/** Is there anything here to fall back to? */
export function available() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Pick a voice for a language, best effort.
 *
 * Voices load asynchronously in Chrome — `getVoices()` is empty on the first call and fills
 * in later — so this returns whatever exists right now and lets the utterance's own `lang`
 * do the work when the list is not ready. Waiting for `voiceschanged` would delay the one
 * thing the user is waiting for.
 */
function pickVoice(language) {
  const wanted = language === 'ar' ? 'ar' : 'en'
  const voices = window.speechSynthesis.getVoices() ?? []
  return (
    voices.find((voice) => voice.lang?.toLowerCase().startsWith(`${wanted}-eg`)) ??
    voices.find((voice) => voice.lang?.toLowerCase().startsWith(wanted)) ??
    null
  )
}

/**
 * Read `text` out loud with the browser's own voice.
 *
 * @param {string} text
 * @param {'ar'|'en'|'mixed'} language
 * @param {{onStart?: () => void, onEnd?: () => void}} handlers
 * @returns {boolean} whether anything was actually spoken
 */
export function speak(text, language, { onStart, onEnd } = {}) {
  if (!available() || !text?.trim()) return false

  // Anything still queued belongs to a reply that has been superseded.
  window.speechSynthesis.cancel()

  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = language === 'ar' ? 'ar-EG' : 'en-US'
  const voice = pickVoice(language)
  if (voice) utterance.voice = voice
  // A shade slower than default: these voices run fast, and this is a fallback being read to
  // someone who is already having a worse-than-usual moment.
  utterance.rate = 0.95

  utterance.onstart = () => onStart?.()
  // `onerror` fires for a cancel as well as a real failure, and both mean the same thing to
  // the caller — stop showing "speaking".
  utterance.onend = () => onEnd?.()
  utterance.onerror = () => onEnd?.()

  window.speechSynthesis.speak(utterance)
  return true
}

export function stop() {
  if (available()) window.speechSynthesis.cancel()
}
