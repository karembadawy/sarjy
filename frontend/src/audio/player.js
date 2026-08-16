/**
 * Ordered playback queue for Sarjy's voice.
 *
 * The backend synthesises a reply chunk by chunk and sends each WAV frame the moment it is
 * ready, so frames arrive while earlier ones are still playing. They must be spoken in the
 * order they arrived and never on top of each other — hence one queue and one cursor.
 *
 * "Speaking" spans the whole reply, not one frame: it starts at the first frame and ends
 * when the queue drains after the backend has said `speak_end`. The recorder and the orb
 * both hang off that single signal (product.md §11 half-duplex).
 *
 * ONE `Audio` element, reused for every frame, and it is deliberate (D-050). iOS grants
 * permission to play to an *element*, not to a page: an element first started inside a user
 * gesture stays playable forever, while a freshly constructed one is refused no matter how
 * many taps preceded it. Creating `new Audio(url)` per frame is therefore silent on iPhone
 * and fine everywhere else — the exact failure this shipped with.
 */

// 10ms of real silence (16-bit mono, 8kHz). Playing it during the tap is what converts the
// element from "refused by iOS" to "allowed", before there is any real audio to play. It has
// actual samples rather than a zero-length data chunk on purpose: an empty file can fail to
// decode, and a `play()` that *errors* inside the gesture unlocks nothing.
const SILENCE =
  'data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'

export class Player {
  /**
   * @param {{onStart?: () => void, onEnd?: () => void}} handlers
   */
  constructor({ onStart, onEnd } = {}) {
    this.onStart = onStart ?? (() => {})
    this.onEnd = onEnd ?? (() => {})
    this.queue = []
    this.playing = false
    this.speaking = false
    // Set when the backend says speak_end: the reply is complete, so when the queue drains
    // we are genuinely finished rather than just waiting for the next frame.
    this.replyComplete = false
    this.urls = []

    this.audio = new Audio()
    // Both paths continue the queue: a frame that fails to decode must not strand the call
    // in "speaking" with the microphone paused.
    this.audio.onended = () => this.#playNext()
    this.audio.onerror = () => this.#playNext()
  }

  /**
   * Must be called SYNCHRONOUSLY from the tap handler, before any `await`.
   *
   * iOS ties the permission to the call stack of a real user gesture; a `play()` issued from
   * a later task — a socket callback, a resolved promise — is refused even though the user
   * did tap. Everything after this point is a socket callback, which is why this cannot wait.
   */
  unlock() {
    this.audio.src = SILENCE
    const started = this.audio.play()
    // Chrome and Safari return a promise; older browsers return undefined. A rejection here
    // is not worth reporting — it only means playback will be attempted the ordinary way.
    started?.then(() => this.audio.pause()).catch(() => {})
  }

  /** A `speak_start` frame arrived: a reply is on its way. */
  begin() {
    this.replyComplete = false
  }

  /** One WAV frame from the socket. */
  enqueue(bytes) {
    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }))
    this.urls.push(url)
    this.queue.push(url)
    if (!this.playing) this.#playNext()
  }

  /** A `speak_end` frame arrived: no more audio is coming for this reply. */
  end() {
    this.replyComplete = true
    // The reply may have been text-only (a synthesis failure, or nothing to say), in which
    // case nothing ever queued and the microphone would stay paused forever without this.
    if (!this.playing) this.#finish()
  }

  /**
   * Stop immediately and drop anything queued.
   *
   * Two callers, one behaviour: hanging up, and barge-in. This is the moment the demo lives
   * or dies — when the user talks over Sarjy, the voice has to stop mid-word, not at the end
   * of the current sentence. So the queue is emptied *and* the element is paused; draining
   * the queue alone would let the frame already playing run to its end.
   */
  stop() {
    this.queue = []
    this.audio.pause()
    this.playing = false
    this.#revoke()
    this.#finish()
  }

  #playNext() {
    const url = this.queue.shift()
    if (!url) {
      this.playing = false
      if (this.replyComplete) this.#finish()
      return
    }

    if (!this.speaking) {
      this.speaking = true
      this.onStart()
    }

    this.playing = true
    this.audio.src = url
    this.audio.play().catch(() => this.#playNext())
  }

  #finish() {
    if (!this.speaking) return
    this.speaking = false
    this.#revoke()
    this.onEnd()
  }

  #revoke() {
    // The element may still hold the last URL as its src; clearing it first keeps the
    // browser from logging a fetch error against a revoked blob.
    this.audio.removeAttribute('src')
    for (const url of this.urls) URL.revokeObjectURL(url)
    this.urls = []
  }
}
