/**
 * The one backend connection of the voice loop. One socket = one call.
 *
 * Protocol (server side lives in backend/app/main.py, recorded as D-039):
 *
 *   up    {type:"hello", user_id, session_id, persona, audio_mime, timeslice_ms}
 *                                                 then binary audio chunks
 *         {type:"bye"} on hang-up
 *   down  {type:"ready"}
 *         {type:"interim", text}                  live partial — render dimmed
 *         {type:"final", text, language}          solidify the bubble, badge it
 *         {type:"reply_text", text, language}     Sarjy's reply, before its audio
 *         {type:"speak_start"} … WAV frames …  {type:"speak_end"}
 *         {type:"stop_speaking"}                  barge-in: drop the queue, stop NOW
 *         {type:"error", key, message_en, message_ar,
 *                        retryable, retry_after_s, transient, speak_again}
 *   up    {type:"retry"}                          answer the last utterance again
 *
 * Phase 4 added `stop_speaking`, and with it the one rule worth remembering about this
 * protocol: a reply ends with EITHER `speak_end` (it finished) OR `stop_speaking` (it was
 * interrupted), never both. The client treats them as the same signal plus, for
 * stop_speaking, "throw away what you have not played yet".
 *
 * Phase 5 widened the error frame instead of adding new message types, because every field
 * on it answers the same question — what should the person be told, and what happens next.
 * `retryable` means a countdown and then `{type:"retry"}`; `transient` means the server is
 * already fixing it; `speak_again` means the reply came out as text and the browser's own
 * voice should read it (see audio/browserVoice.js).
 */

const URL_BASE = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws'

export class VoiceSocket {
  /**
   * @param {Object} handlers  one per protocol message, plus onOpen/onClose/onSocketError
   */
  constructor(handlers = {}) {
    this.handlers = handlers
    this.socket = null
    this.hungUp = false
  }

  connect({ userId, sessionId, persona, audioMime, timesliceMs }) {
    this.hungUp = false
    const socket = new WebSocket(URL_BASE)
    // WAV frames arrive as binary; without this they surface as Blobs and every consumer
    // has to await them.
    socket.binaryType = 'arraybuffer'
    this.socket = socket

    socket.onopen = () => {
      // audio_mime is reported so a phone that records something Deepgram cannot decode is
      // diagnosable from the server log rather than by guessing at silence (D-050).
      socket.send(
        JSON.stringify({
          type: 'hello',
          user_id: userId,
          session_id: sessionId,
          persona,
          audio_mime: audioMime,
          // How much audio the first chunk contains. The server needs it to place Deepgram's
          // stream clock on the wall clock, which is what makes the recognition timing in
          // `turn_metrics` a real number rather than a guess (D-017).
          timeslice_ms: timesliceMs,
        }),
      )
    }

    socket.onmessage = (event) => {
      // Any traffic at all counts as the call being alive — it is what resets the idle
      // timer that hangs up after a minute of silence (product.md §4).
      this.handlers.onActivity?.()
      if (event.data instanceof ArrayBuffer) {
        this.handlers.onAudio?.(event.data)
        return
      }
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      switch (message.type) {
        case 'ready':
          this.handlers.onReady?.()
          break
        case 'interim':
          this.handlers.onInterim?.(message.text)
          break
        case 'final':
          this.handlers.onFinal?.(message.text, message.language)
          break
        case 'reply_text':
          this.handlers.onReplyText?.(message.text, message.language)
          break
        case 'speak_start':
          this.handlers.onSpeakStart?.()
          break
        case 'speak_end':
          this.handlers.onSpeakEnd?.()
          break
        case 'stop_speaking':
          this.handlers.onStopSpeaking?.()
          break
        case 'error':
          this.handlers.onError?.(message)
          break
        default:
          break
      }
    }

    socket.onerror = () => {
      // The browser deliberately hides the reason for a failed WebSocket handshake, so
      // there is nothing more specific to report than "it did not connect".
      this.handlers.onSocketError?.()
    }

    socket.onclose = () => {
      this.socket = null
      // A close the user asked for is not a fault; only an unexpected drop is.
      this.handlers.onClose?.(this.hungUp)
    }
  }

  /** Binary audio chunk up. Dropped silently while the socket is not open. */
  sendAudio(chunk) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(chunk)
  }

  /**
   * Ask for the last utterance to be answered again (the rate-limit auto-retry).
   *
   * The server still holds what was said, so a retry costs the person nothing — they do not
   * have to repeat a sentence they already spoke because Gemini was busy for four seconds.
   */
  requestRetry() {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'retry' }))
    }
  }

  hangUp() {
    this.hungUp = true
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'bye' }))
    }
    this.socket?.close()
    this.socket = null
  }
}
