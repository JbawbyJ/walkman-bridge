import test from 'node:test'
import assert from 'node:assert/strict'
import { artworkUrl, linkImportUrl, supplementalMetadata } from './mediaImport.js'
import { api } from './api.js'

test('link submission uses the authenticated same-origin JSON API', async () => {
  const previous = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => { request = { url, options }; return { ok: true, status: 202, json: async () => ({ job_id: 'admitted' }) } }
  try {
    assert.deepEqual(await api.importLink('https://youtu.be/abcdefghijk'), { job_id: 'admitted' })
    assert.equal(request.url, '/api/media/import-link')
    assert.equal(request.options.credentials, 'same-origin')
    assert.equal(request.options.method, 'POST')
    assert.equal(request.options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(request.options.body), { url: 'https://youtu.be/abcdefghijk' })
  } finally { globalThis.fetch = previous }
})

test('the link form accepts supported HTTPS origins and leaves extractor decisions to the service', () => {
  assert.equal(linkImportUrl('  https://www.youtube.com/watch?v=abcdefghijk  '), 'https://www.youtube.com/watch?v=abcdefghijk')
  assert.equal(linkImportUrl('https://soundcloud.com/artist/track'), 'https://soundcloud.com/artist/track')
  for (const input of ['', 'http://youtu.be/abcdefghijk', 'https://youtube.com.evil.test/watch?v=x', 'https://user:password@youtube.com/watch?v=x', 'https://youtube.com:444/watch?v=x', 'https://127.0.0.1/song', 'javascript:alert(1)', 'https://youtube.com/watch?v=a\n&x=y']) assert.throws(() => linkImportUrl(input))
})

test('artwork can only use the ready media item own authenticated endpoint', () => {
  const item = { id: 'one', status: 'ready', scan: { ok: true }, artwork_url: '/api/media/one/artwork' }
  assert.equal(artworkUrl(item), '/api/media/one/artwork')
  assert.equal(artworkUrl({ ...item, status: 'scanning' }), null)
  assert.equal(artworkUrl({ ...item, scan: { ok: false } }), null)
  for (const url of ['https://example.com/cover.jpg', '//example.com/cover.jpg', 'data:image/svg+xml,<svg/>', '/api/media/other/artwork', '/api/media/one/artwork?redirect=https://example.com', '/api/media/one/../artwork']) assert.equal(artworkUrl({ ...item, artwork_url: url }), null)
  assert.equal(artworkUrl({ ...item, artwork_url: null }), null)
})

test('supplemental metadata omits missing fields and keeps labels concise', () => {
  assert.equal(supplementalMetadata({ genre: 'Ambient', year: '2024', track: '2/10' }), 'Ambient · 2024 · Track 2/10')
  assert.equal(supplementalMetadata({}), '')
  assert.equal(supplementalMetadata({ year: 2024, track: 3 }), '2024 · Track 3')
})
