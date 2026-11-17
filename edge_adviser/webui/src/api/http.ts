// Thin typed HTTP client against the FastAPI backend.
//
// The backend returns a uniform envelope {ok, data?, error?}. Helpers here unwrap
// it so views get `data` directly or throw a readable Chinese error. The auth
// token (optional shared PIN) is attached automatically from sessionStorage.

const BASE = import.meta.env.DEV ? '' : '/dist'

export interface Envelope<T = unknown> {
  ok: boolean
  data: T | null
  error: string | null
}

let _token = ''

export function setToken(t: string) {
  _token = t
  if (t) sessionStorage.setItem('ea_token', t)
  else sessionStorage.removeItem('ea_token')
}

export function initToken() {
  _token = sessionStorage.getItem('ea_token') || ''
}

export function getToken() {
  return _token
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (_token) h['Authorization'] = `Bearer ${_token}`
  return h
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method,
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const env = (await res.json()) as Envelope<T>
  if (env.ok) return env.data as T
  // treat unauth specially so the app can prompt for PIN
  if (res.status === 401) {
    throw new UnauthorizedError(env.error || 'unauthorized')
  }
  throw new ApiError(env.error || `请求失败 (${res.status})`)
}

export class ApiError extends Error {}
export class UnauthorizedError extends Error {}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
}

// SSE subscription: returns a close() handle. `onEvent` receives parsed payloads.
export function connectSSE(path: string, onEvent: (kind: string, data: any, id?: string) => void): () => void {
  const url = `${BASE}${path}`
  const es = new EventSource(url, { withCredentials: false })
  es.onmessage = (e) => {
    try {
      onEvent('message', JSON.parse(e.data), e.lastEventId)
    } catch (err) {
      onEvent('message', e.data, e.lastEventId)
    }
  }
  // bus.stream uses named events; fall back to default for robustness
  for (const kind of ['snapshot', 'alert', 'task', 'ping']) {
    es.addEventListener(kind, (e: MessageEvent) => {
      if (kind === 'ping') return
      try {
        onEvent(kind, JSON.parse(e.data), e.lastEventId)
      } catch (err) {
        onEvent(kind, e.data, e.lastEventId)
      }
    })
  }
  return () => es.close()
}