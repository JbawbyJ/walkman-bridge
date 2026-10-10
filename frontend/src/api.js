// All HTTP and media URL construction stays here. The desktop chooses its
// loopback port, and authenticates renderer requests with a same-origin cookie.
const BASE = '/api'
const idPath = id => encodeURIComponent(id)

async function req(path, options = {}, includeHeaders = false) {
  const response = await fetch(`${BASE}${path}`, { credentials: 'same-origin', ...options })
  let body
  try { body = response.status === 204 ? null : await response.json() } catch { body = null }
  if (!response.ok) {
    const detail = body?.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status}).`)
    error.status = response.status
    error.code = detail?.code
    throw error
  }
  return includeHeaders ? { items: body, etag: response.headers.get('etag') } : body
}

const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => req('/health'),
  playbackState: () => req('/playback-state'),
  savePlaybackState: state => req('/playback-state', { ...json('PATCH', state), keepalive: true }),
  acquirePlaybackLease: media_id => req('/playback/lease', json('POST', { media_id })),
  releasePlaybackLease: media_id => req('/playback/lease', { ...json('DELETE', { media_id }), keepalive: true }),
  queue: () => req('/queue'),
  importLink: url => req('/media/import-link', json('POST', { url })),
  importFiles: files => {
    const body = new FormData()
    for (const file of files) body.append('files', file)
    return req('/media/import', { method: 'POST', body })
  },
  removeQueueItem: id => req(`/queue/items/${idPath(id)}`, { method: 'DELETE' }),
  orderQueue: ids => req('/queue/order', json('PATCH', { ids })),
  rescan: id => req(`/media/${idPath(id)}/rescan`, { method: 'POST' }),
  prepare: id => req(`/media/${idPath(id)}/prepare`, json('POST', { format: 'flac' })),
  transfer: media_ids => req('/transfers', json('POST', { media_ids })),
  device: () => req('/device'),
  tracks: () => req('/tracks', {}, true),
  deleteTrack: (id, etag) => req(`/tracks/${idPath(id)}`, { method: 'DELETE', headers: { 'If-Match': etag } }),
  backup: () => req('/backup', { method: 'POST' }),
  job: id => req(`/jobs/${idPath(id)}`),
  latestJob: () => req('/jobs/latest'),
  operations: () => req('/engine-busy'),
  playlists: () => req('/playlists'),
  createPlaylist: (name, media_ids = []) => req('/playlists', json('POST', { name, media_ids })),
  updatePlaylist: (id, changes, etag) => req(`/playlists/${idPath(id)}`, { ...json('PATCH', changes), headers: { 'Content-Type': 'application/json', 'If-Match': etag } }),
  deletePlaylist: (id, etag) => req(`/playlists/${idPath(id)}`, { method: 'DELETE', headers: { 'If-Match': etag } }),
  updateMetadata: (id, changes) => req(`/media/${idPath(id)}/metadata`, json('PATCH', changes)),
  devicePlaylists: async () => { const result = await req('/device/playlists', {}, true); return { ...result.items, etag: result.etag } },
  createDevicePlaylist: (changes, etag) => req('/device/playlists', { ...json('POST', changes), headers: { 'Content-Type': 'application/json', 'If-Match': etag } }),
  updateDevicePlaylist: (id, changes, etag) => req(`/device/playlists/${idPath(id)}`, { ...json('PATCH', changes), headers: { 'Content-Type': 'application/json', 'If-Match': etag } }),
  deleteDevicePlaylist: (id, etag) => req(`/device/playlists/${idPath(id)}`, { method: 'DELETE', headers: { 'If-Match': etag } }),
  mediaUrl: (id, revision = '') => `${BASE}/media/${idPath(id)}/stream${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`,
}
