/**
 * Microphone capture: chunks up the WebSocket, live level for the orb.
 *
 * The microphone stays open for the whole call, including while Sarjy is talking. That is
 * what makes barge-in possible, and it is a deliberate reversal of D-040, which paused the
 * recorder for the length of every reply. Pausing removed the echo path completely — and
 * removed interruption with it, which is the one thing that makes a voice assistant feel
 * like a phone call rather than a form (product.md §11, D-012).
 *
 * What defends against Sarjy hearing itself now:
 *   1. echoCancellation + noiseSuppression on the input stream, below.
 *   2. The server's barge-in bar — two real words above a confidence floor (backend
 *      app/barge_in.py). Whatever leaks past the browser's echo canceller has to look like
 *      a person talking before it can cut a reply off.
 *   3. Moderate speaker volume, which is documented demo hygiene and not code (§11.3).
 *
 * The mute button is separate and absolute: it disables the audio tracks, so nothing is
 * captured and nothing is sent, however loud the room is.
 */

// MediaRecorder emits one blob per timeslice. 250ms keeps Deepgram's interim transcripts
// feeling live without flooding the socket with tiny frames. It is reported to the server in
// the hello frame, which uses it to line Deepgram's stream clock up with the wall clock.
export const TIMESLICE_MS = 250

// Small FFT: we want one loudness number for the orb, not a spectrum.
const FFT_SIZE = 256

// Ordered by preference, and the order matters more than it looks: Deepgram's live API
// decodes Opus in a WebM or Ogg container from the header alone, but does NOT decode AAC —
// which is the only thing Safari could record before 18.4 (audio/mp4). So MP4 is last: it is
// a "better than refusing to record" fallback, not a working path (D-050).
const CANDIDATE_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/ogg;codecs=opus',
  'audio/webm',
  'audio/mp4',
]

/** The container this browser will actually record in, or null if it cannot record at all. */
export function supportedMimeType() {
  if (typeof MediaRecorder === 'undefined') return null
  return CANDIDATE_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null
}

export class Recorder {
  /**
   * @param {(chunk: ArrayBuffer) => void} onChunk  called ~4x/second with encoded audio
   */
  constructor(onChunk) {
    this.onChunk = onChunk
    this.stream = null
    this.recorder = null
    this.audioContext = null
    this.analyser = null
    this.levels = null
    this.muted = false
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })

    const mimeType = supportedMimeType()
    this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined)

    this.recorder.ondataavailable = async (event) => {
      // A muted or paused recorder should produce nothing the backend can transcribe.
      if (!event.data?.size || this.muted) return
      this.onChunk(await event.data.arrayBuffer())
    }

    this.recorder.start(TIMESLICE_MS)

    // Level metering runs off the same stream, independent of the recorder, so the orb
    // keeps responding to the room even while the recorder is paused.
    this.audioContext = new (window.AudioContext ?? window.webkitAudioContext)()
    const source = this.audioContext.createMediaStreamSource(this.stream)
    this.analyser = this.audioContext.createAnalyser()
    this.analyser.fftSize = FFT_SIZE
    this.analyser.smoothingTimeConstant = 0.75
    source.connect(this.analyser)
    this.levels = new Uint8Array(this.analyser.frequencyBinCount)
  }

  /** Current microphone loudness, 0..1 — drives the listening ripples. */
  level() {
    if (!this.analyser || this.muted) return 0
    this.analyser.getByteFrequencyData(this.levels)
    let sum = 0
    for (const value of this.levels) sum += value
    // Root-of-mean rather than raw mean: speech barely moves a linear average, and the orb
    // has to look alive at ordinary talking volume.
    return Math.min(1, Math.sqrt(sum / this.levels.length / 255) * 1.4)
  }

  /** Silence the microphone without tearing the session down (mute button).
   *
   * Both halves matter. Disabling the tracks stops the browser capturing anything at all,
   * and the `muted` flag stops the silent frames MediaRecorder still emits from being sent —
   * so a muted call costs no Deepgram minutes either.
   */
  setMuted(muted) {
    this.muted = muted
    for (const track of this.stream?.getAudioTracks() ?? []) {
      track.enabled = !muted
    }
  }

  stop() {
    if (this.recorder?.state !== 'inactive') this.recorder?.stop()
    for (const track of this.stream?.getTracks() ?? []) track.stop()
    this.audioContext?.close()
    this.stream = null
    this.recorder = null
    this.analyser = null
  }
}
