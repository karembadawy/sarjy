/**
 * The small REST surface behind the memory drawer, the bookings panel and the persona
 * toggle. The voice loop itself is the WebSocket in ws.js — nothing here is on the
 * latency path of a spoken turn.
 *
 * The base URL is derived from VITE_WS_URL rather than configured separately: they are the
 * same backend, and two variables that must agree is one variable that eventually will not
 * (the Vercel dashboard is not a place where a typo announces itself). VITE_API_URL still
 * overrides it, for the case where they genuinely differ.
 */

const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws'

export const API_BASE =
  import.meta.env.VITE_API_URL ?? WS_URL.replace(/^ws/, 'http').replace(/\/ws$/, '')

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) throw new Error(`${options?.method ?? 'GET'} ${path} → ${response.status}`)
  return response.status === 204 ? null : response.json()
}

export const getFacts = (userId) => request(`/api/users/${userId}/facts`)

export const forgetFact = (userId, key) =>
  request(`/api/users/${userId}/facts/${encodeURIComponent(key)}`, { method: 'DELETE' })

export const getBookings = (userId) => request(`/api/users/${userId}/bookings`)

export const setPersona = (userId, persona) =>
  request(`/api/users/${userId}/persona`, {
    method: 'PUT',
    body: JSON.stringify({ persona }),
  })
