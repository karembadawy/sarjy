/**
 * Identity without accounts (D-011 / product.md §5).
 *
 *   user_id     one UUID per browser, kept in localStorage — this is what memory hangs off.
 *   session_id  a fresh UUID per page load — one "call", one row in `sessions`.
 *
 * Stated limitation for the writeup: memory is per-browser. Real accounts are the
 * production path.
 */

const USER_KEY = 'sarjy_user_id'

function uuid() {
  // Available in every browser this app targets; the fallback covers plain-HTTP origins,
  // where crypto.randomUUID is not exposed.
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
    (c ^ (globalThis.crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16),
  )
}

export function getOrCreateUserId() {
  let id = null
  try {
    id = localStorage.getItem(USER_KEY)
  } catch {
    // Private mode / blocked storage: fall through to an in-memory id for this page load.
  }
  if (!id) {
    id = uuid()
    try {
      localStorage.setItem(USER_KEY, id)
    } catch {
      /* nothing to do — the user is simply not remembered after a reload */
    }
  }
  return id
}

export function newSessionId() {
  return uuid()
}
