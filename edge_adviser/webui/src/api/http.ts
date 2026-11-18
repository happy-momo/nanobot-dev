// Thin typed HTTP client against the FastAPI backend.
//
// The backend returns a uniform envelope {ok, data?, error?}. Helpers here unwrap
// it so views get `data` directly or throw a readable Chinese error. The auth
// token (optional shared PIN) is attached automatically from sessionStorage.

// The SPA is served from "/" (FastAPI FileResponse at root) and assets under
// /dist, but the API and SSE always live at the absolute paths "/api/*" and
// "/stream/*" — independent of the Vite base. So the request BASE is empty in
// both dev and production: a fetch("/api/...") hits the backend directly.
// (Using the Vite base here would turn the API path into "/dist/api/..." and 404.)
const BASE = ''

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
  let res: Response
  try {
    res = await fetch(`${BASE}${url}`, {
      method,
      headers: headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('无法连接服务器，请检查网络或服务是否运行')
  }
  let env: Envelope<T>
  try {
    env = (await res.json()) as Envelope<T>
  } catch {
    // non-JSON body (e.g. an HTML error page from a proxy) → readable error
    throw new ApiError(res.ok ? '服务器返回了无法解析的响应' : `请求失败 (${res.status})`)
  }
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

// Authenticated download: window.open() cannot send the bearer token, so
// PIN-protected deployments would 401. Fetch → blob → object URL instead.
export async function downloadFile(url: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${url}`, { headers: headers() })
  if (res.status === 401) throw new UnauthorizedError('unauthorized')
  if (!res.ok) throw new ApiError(`下载失败 (${res.status})`)
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}

export type SSEStatus = 'connecting' | 'open' | 'error'

// SSE subscription: returns a close() handle. `onEvent` receives parsed payloads.
// `onStatus` reports connection health (EventSource auto-reconnects on error).
// The token rides as ?token= because EventSource cannot send headers; the
// backend PIN gate accepts the query fallback.
export function connectSSE(
  path: string,
  onEvent: (kind: string, data: any, id?: string) => void,
  onStatus?: (st: SSEStatus) => void,
): () => void {
  const sep = path.includes('?') ? '&' : '?'
  const url = `${BASE}${path}${sep}token=${encodeURIComponent(_token || '')}`
  onStatus?.('connecting')
  const es = new EventSource(url, { withCredentials: false })
  es.onopen = () => onStatus?.('open')
  es.onerror = () => onStatus?.('error')
  es.onmessage = (e) => {
    try {
      onEvent('message', JSON.parse(e.data), e.lastEventId)
    } catch {
      onEvent('message', e.data, e.lastEventId)
    }
  }
  // bus.stream uses named events; fall back to default for robustness
  for (const kind of ['snapshot', 'alert', 'task', 'ping']) {
    es.addEventListener(kind, (e: MessageEvent) => {
      if (kind === 'ping') return
      try {
        onEvent(kind, JSON.parse(e.data), e.lastEventId)
      } catch {
        onEvent(kind, e.data, e.lastEventId)
      }
    })
  }
  return () => es.close()
}
