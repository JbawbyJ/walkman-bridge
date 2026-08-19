const BASE = '/api'

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} — ${text}`)
  }
  return res.json()
}

export const api = {
  device: () => req('/device'),
  tracks: () => req('/tracks'),
  deleteTrack: (id) => req(`/tracks/${id}`, { method: 'DELETE' }),
  upload: (files) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    return req('/upload', { method: 'POST', body: fd })
  },
  job: (id) => req(`/jobs/${id}`),
}
