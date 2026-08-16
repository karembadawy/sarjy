import { useCallback, useEffect, useRef, useState } from 'react'

import { setPersona as savePersona } from './api'
import { getOrCreateUserId, newSessionId } from './identity'
import * as browserVoice from './audio/browserVoice'
import { Player } from './audio/player'
import { Recorder, supportedMimeType, TIMESLICE_MS } from './audio/recorder'
import { VoiceSocket } from './ws'
import MemoryDrawer from './components/MemoryDrawer'
import MessageBubble from './components/MessageBubble'
import Notice from './components/Notice'
import Orb from './components/Orb'
import PersonaToggle from './components/PersonaToggle'

/**
 * The call screen. Tap the orb once, then talk — the loop is hands-free from there, and
 * since Phase 4 you can talk straight over Sarjy to interrupt it.
 */

// idle → connecting → listening ⇄ thinking → speaking ⇄ listening → ended
const CALL_IS_LIVE = new Set(['listening', 'thinking', 'speaking'])

// The call hangs up after a minute of nobody saying anything. It is the free tiers' friend
// (Deepgram bills the open connection, not the speech) and it is also just how a phone
// behaves. Any traffic on the socket resets it — the user talking, Sarjy answering.
const SILENCE_TIMEOUT_MS = 60_000

const PERSONA_KEY = 'sarjy_persona'

const HINTS = {
  idle: ['اضغط عشان تتكلم', 'Tap the orb and just talk'],
  connecting: ['بيوصل...', 'Connecting…'],
  listening: ['اتكلم، أنا سامعك', 'Listening — speak in Arabic or English'],
  thinking: ['ثانية واحدة...', 'Thinking…'],
  speaking: ['سرجي بيتكلم — تقدر تقاطعه', 'Sarjy is speaking — talk over it to interrupt'],
  ended: ['المكالمة قفلت', 'Call ended — tap to start again'],
}

export default function App() {
  const [userId] = useState(getOrCreateUserId)
  // A fresh session per call — one row in `sessions`.
  const sessionIdRef = useRef(newSessionId())

  const [state, setState] = useState('idle')
  const [messages, setMessages] = useState([])
  const [interim, setInterim] = useState('')
  const [muted, setMuted] = useState(false)
  // One designed state for everything that can run out or drop — see components/Notice.jsx.
  const [notice, setNotice] = useState(null)
  const [persona, setPersona] = useState(
    () => localStorage.getItem(PERSONA_KEY) ?? 'egyptian',
  )
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Bumped after every completed turn so the drawer reloads: a booking made by voice should
  // show up in the panel while the spoken confirmation is still playing.
  const [turnCount, setTurnCount] = useState(0)

  const socketRef = useRef(null)
  const recorderRef = useRef(null)
  const playerRef = useRef(null)
  const bottomRef = useRef(null)
  const idleTimerRef = useRef(null)
  // The last thing Sarjy said, so the browser's own voice can read it when synthesis has
  // nothing left to give (D-005's last fallback).
  const lastReplyRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, interim])

  const getLevel = useCallback(() => recorderRef.current?.level() ?? 0, [])

  const teardown = useCallback((nextState = 'idle') => {
    clearTimeout(idleTimerRef.current)
    playerRef.current?.stop()
    recorderRef.current?.stop()
    socketRef.current?.hangUp()
    browserVoice.stop()
    playerRef.current = null
    recorderRef.current = null
    socketRef.current = null
    setInterim('')
    setState(nextState)
  }, [])

  const settle = useCallback(
    () => setState((current) => (current === 'idle' || current === 'ended' ? current : 'listening')),
    [],
  )

  /** The rate-limit countdown expired: ask for the same utterance again (see ws.js). */
  const retryTurn = useCallback(() => {
    setNotice(null)
    if (!socketRef.current) return
    socketRef.current.requestRetry()
    setState('thinking')
  }, [])

  // A tab closed mid-call must still release the microphone and end the session row.
  useEffect(() => () => teardown(), [teardown])

  const touch = useCallback(() => {
    clearTimeout(idleTimerRef.current)
    idleTimerRef.current = setTimeout(() => teardown('ended'), SILENCE_TIMEOUT_MS)
  }, [teardown])

  async function startCall() {
    setNotice(null)
    setState('connecting')
    // Each call is its own session, so hanging up and tapping again starts a clean one.
    sessionIdRef.current = newSessionId()

    const player = new Player({
      // The microphone is NOT paused here any more (D-040 is superseded): staying open
      // through playback is what lets the user cut in. Echo is handled by the browser's
      // canceller and by the server's two-word barge-in bar.
      onStart: () => setState('speaking'),
      onEnd: () => {
        settle()
        setTurnCount((count) => count + 1)
      },
    })
    playerRef.current = player
    // Synchronously, inside the tap: iOS only lets an Audio element play if it was first
    // started from a real user gesture, and every later `play()` here comes from a socket
    // callback. Without this the iPhone shows the transcript and stays silent (D-050).
    player.unlock()

    const socket = new VoiceSocket({
      onActivity: touch,
      onReady: async () => {
        try {
          const recorder = new Recorder((chunk) => socket.sendAudio(chunk))
          await recorder.start()
          recorder.setMuted(muted)
          recorderRef.current = recorder
          setState('listening')
          touch()
        } catch {
          setNotice({
            message_ar: 'محتاج إذن الميكروفون عشان أسمعك. اسمح للموقع وجرّب تاني.',
            message_en: 'Sarjy needs microphone permission. Allow it and tap again.',
          })
          teardown()
        }
      },
      onInterim: (text) => setInterim(text),
      onFinal: (text, language) => {
        setInterim('')
        setMessages((current) => [...current, { role: 'user', text, language }])
        // A new thing to say supersedes whatever went wrong on the last one.
        setNotice(null)
        setState('thinking')
      },
      onReplyText: (text, language) => {
        lastReplyRef.current = { text, language }
        setMessages((current) => [...current, { role: 'assistant', text, language }])
      },
      onSpeakStart: () => player.begin(),
      onAudio: (bytes) => player.enqueue(bytes),
      onSpeakEnd: () => player.end(),
      // Barge-in. The reply was cut off server-side; everything queued here is now stale
      // audio for a turn that no longer exists, so it goes immediately.
      onStopSpeaking: () => {
        player.stop()
        browserVoice.stop()
        setInterim('')
        settle()
      },
      onError: (message) => {
        setNotice(message)
        // Errors are per-turn, not fatal: the call stays up so the user can try again.
        setState((current) => (CALL_IS_LIVE.has(current) ? 'listening' : current))

        // The synthesis chain is spent, so the reply exists only as text. Reading it with
        // the browser's own voice is the last link of D-005 — a slightly robotic sentence
        // beats a call that has gone quiet on you.
        if (message.speak_again && lastReplyRef.current) {
          const { text, language } = lastReplyRef.current
          browserVoice.speak(text, language, {
            onStart: () => setState('speaking'),
            onEnd: () => {
              settle()
              setTurnCount((count) => count + 1)
            },
          })
        }
      },
      onSocketError: () =>
        setNotice({
          message_ar: 'مش قادر أوصل بسرجي. اتأكد إن السيرفر شغال وجرّب تاني.',
          message_en: 'Could not reach Sarjy. Check the server is running and tap again.',
        }),
      onClose: (expected) => {
        if (!expected) {
          setNotice({
            message_ar: 'الاتصال قطع. اضغط تاني عشان ترجع.',
            message_en: 'The connection dropped. Tap the orb to reconnect.',
          })
        }
        teardown()
      },
    })
    socketRef.current = socket
    // The container is decided by the browser, not by us, and the microphone is not open
    // yet — `supportedMimeType()` is a static capability check, so it can travel in hello.
    socket.connect({
      userId,
      sessionId: sessionIdRef.current,
      persona,
      audioMime: supportedMimeType(),
      timesliceMs: TIMESLICE_MS,
    })
  }

  function toggleCall() {
    if (CALL_IS_LIVE.has(state)) teardown()
    else startCall()
  }

  function toggleMute() {
    const next = !muted
    setMuted(next)
    recorderRef.current?.setMuted(next)
  }

  function choosePersona(key) {
    setPersona(key)
    localStorage.setItem(PERSONA_KEY, key)
    // Fire and forget: the row is what the next turn reads, and a failed write just means
    // Sarjy keeps the dialect it already had — not something to interrupt a call over.
    savePersona(userId, key).catch(() => {})
  }

  const [hintAr, hintEn] = HINTS[state] ?? HINTS.idle
  const live = CALL_IS_LIVE.has(state)

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-ink-800/80 px-4 py-4 sm:px-8">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold tracking-tight text-amber">سرجي</span>
          <span className="hidden text-sm tracking-[0.35em] text-muted uppercase sm:inline">
            Sarjy
          </span>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          <PersonaToggle persona={persona} onChange={choosePersona} />

          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="rounded-full border border-ink-700 px-3 py-1.5 text-sm text-muted transition-colors hover:border-ink-600 hover:text-cream"
            title="اللي سرجي فاكره عنك · What Sarjy remembers"
          >
            <span aria-hidden>🧠</span>
            <span className="ml-1.5 hidden sm:inline">ذاكرة</span>
          </button>

          <div className="flex items-center gap-2 text-xs text-muted">
            <span
              aria-hidden
              className={`h-2 w-2 rounded-full transition-colors ${
                live ? 'bg-amber shadow-[0_0_8px_var(--color-amber)]' : 'bg-ink-600'
              }`}
            />
            <span className="hidden sm:inline">{live ? 'متصل · live' : 'مقفول · idle'}</span>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col overflow-hidden px-4 sm:px-6">
        <div className="flex flex-col items-center gap-4 pt-8 pb-6">
          <Orb
            state={state === 'ended' ? 'idle' : state}
            getLevel={getLevel}
            onTap={toggleCall}
            disabled={state === 'connecting'}
          />

          <div className="text-center">
            <p className="text-cream/85">{hintAr}</p>
            <p className="text-sm text-muted">{hintEn}</p>
          </div>

          {live && (
            <button
              type="button"
              onClick={toggleMute}
              aria-pressed={muted}
              className={`rounded-full border px-4 py-1.5 text-sm transition-colors ${
                muted
                  ? 'border-amber/50 bg-amber/15 text-amber'
                  : 'border-ink-600 text-muted hover:border-ink-600/80 hover:text-cream'
              }`}
            >
              {muted ? 'الميكروفون مقفول · Muted' : 'اقفل الميكروفون · Mute'}
            </button>
          )}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto border-t border-ink-800/60 py-6">
          {messages.length === 0 && !interim && (
            <p className="pt-6 text-center text-sm text-muted/60">
              الكلام هيظهر هنا · your conversation appears here
            </p>
          )}

          {messages.map((message, index) => (
            <MessageBubble key={index} {...message} />
          ))}

          {/* The live partial: same bubble, dimmed, replaced in place until it solidifies. */}
          {interim && <MessageBubble role="user" text={interim} language={null} pending />}

          <Notice notice={notice} onRetry={retryTurn} onDismiss={() => setNotice(null)} />

          <div ref={bottomRef} />
        </div>

        <p className="pb-4 font-mono text-[0.65rem] text-muted/40">
          user {userId.slice(0, 8)} · session {sessionIdRef.current.slice(0, 8)}
        </p>
      </main>

      <MemoryDrawer
        userId={userId}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        refreshKey={turnCount}
      />
    </div>
  )
}
