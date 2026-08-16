/**
 * "What I remember about you" + the bookings panel (product.md §4).
 *
 * One slide-over holding both, because they answer the same question from two sides: what
 * has this thing actually stored about me. The facts are the memory of §9 and the bookings
 * are the rows `create_booking` wrote — the point of showing them is that they are real,
 * and that a fact can be deleted by the person it is about.
 *
 * Both lists are read straight from the API the brain reads, so there is no second source
 * of truth to drift.
 */

import { useCallback, useEffect, useState } from 'react'

import { forgetFact, getBookings, getFacts } from '../api'

const WHEN = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
})

export default function MemoryDrawer({ userId, open, onClose, refreshKey }) {
  const [facts, setFacts] = useState([])
  const [bookings, setBookings] = useState([])
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      const [nextFacts, nextBookings] = await Promise.all([
        getFacts(userId),
        getBookings(userId),
      ])
      setFacts(nextFacts)
      setBookings(nextBookings)
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [userId])

  // Reload when it opens, and again whenever a turn completes: a booking made by voice
  // should appear here while the confirmation is still being spoken.
  useEffect(() => {
    if (open) load()
  }, [open, load, refreshKey])

  async function forget(key) {
    setFacts((current) => current.filter((fact) => fact.key !== key))
    try {
      await forgetFact(userId, key)
    } catch {
      load() // it is still there — put it back rather than lie about it
    }
  }

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="fixed inset-0 z-10 cursor-default bg-ink-950/70 backdrop-blur-sm"
        />
      )}

      <aside
        aria-hidden={!open}
        className={`fixed top-0 right-0 z-20 flex h-dvh w-[min(24rem,88vw)] flex-col border-l border-ink-800 bg-ink-900 transition-transform duration-300 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <header className="flex items-center justify-between border-b border-ink-800 px-5 py-4">
          <div>
            <p className="text-cream">اللي سرجي فاكره عنك</p>
            <p className="text-xs text-muted">What Sarjy remembers about you</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-2 py-1 text-muted hover:text-cream"
            aria-label="اقفل · Close"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-8 overflow-y-auto px-5 py-6">
          <section>
            <h2 className="mb-3 font-mono text-[0.65rem] tracking-widest text-amber/60 uppercase">
              حقائق · Facts
            </h2>
            {facts.length === 0 && (
              <p className="text-sm text-muted/70">
                لسه مفيش حاجة اتخزنت · nothing stored yet
              </p>
            )}
            <ul className="space-y-2">
              {facts.map((fact) => (
                <li
                  key={fact.key}
                  className="group flex items-start gap-3 rounded-xl bg-ink-800/60 px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-[0.7rem] text-muted">{fact.key}</p>
                    <p dir="auto" className="text-sm break-words text-cream">
                      {fact.value}
                    </p>
                  </div>
                  {fact.source_language && (
                    <span className="mt-1 font-mono text-[0.6rem] tracking-wider text-amber/40 uppercase">
                      {fact.source_language}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => forget(fact.key)}
                    aria-label={`انسى ${fact.key} · forget ${fact.key}`}
                    className="mt-0.5 rounded-full px-1.5 text-muted transition-colors hover:text-red-300"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="mb-3 font-mono text-[0.65rem] tracking-widest text-amber/60 uppercase">
              مواعيد · Bookings
            </h2>
            {bookings.length === 0 && (
              <p className="text-sm text-muted/70">مفيش مواعيد جاية · no upcoming bookings</p>
            )}
            <ul className="space-y-2">
              {bookings.map((booking) => (
                <li key={booking.id} className="rounded-xl bg-ink-800/60 px-3 py-2">
                  <p dir="auto" className="text-sm text-cream">
                    {booking.service}
                  </p>
                  <p className="text-xs text-amber/70">
                    {WHEN.format(new Date(booking.scheduled_at))}
                  </p>
                  {booking.notes && (
                    <p dir="auto" className="mt-1 text-xs text-muted">
                      {booking.notes}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>

          {failed && (
            <p dir="auto" className="rounded-xl bg-red-500/10 px-3 py-2 text-xs text-red-200">
              مش قادر أوصل للذاكرة دلوقتي · could not reach the server
            </p>
          )}
        </div>
      </aside>
    </>
  )
}
