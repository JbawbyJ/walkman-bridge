import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { Simulate } from 'react-dom/test-utils'
import MusicManager from '../src/components/MusicManager.jsx'
import { api } from '../src/api.js'

test('metadata editor identifies sources and only saves intentional edits, including clearing a tag', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' })
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true })) {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true }
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false }
  const original = { ...api }, saved = []
  api.playlists = async () => ({ items: [] })
  api.updateMetadata = async (...args) => saved.push(args)
  const item = { id: 'one', title: '夜の歌', artist: 'Unknown artist', album: 'My album', genre: 'Ambient', year: 2024, name: 'song.mp4',
    metadata_provenance: { title: 'provider.youtube.title', artist: 'missing', album: 'user_edit', genre: 'embedded', year: 'embedded', track: 'missing' } }
  const host = document.querySelector('#root'), root = createRoot(host)
  const button = label => [...host.querySelectorAll('button')].find(el => el.textContent === label)
  const input = label => host.querySelector(`.manager-editor input[aria-label="${label}"]`)
  try {
    await act(async () => root.render(<MusicManager product="player" items={[item]} />))
    await act(async () => button('Edit details').click())
    assert.match(host.textContent, /YouTube metadata/)
    assert.match(host.textContent, /Not supplied by the source/)
    assert.match(host.textContent, /Your edit/)
    assert.match(host.textContent, /Embedded file tags/)
    assert.equal(input('Artist').value, '')
    assert.equal(input('Artist').placeholder, 'Not supplied')
    assert.equal(input('Album').value, 'My album')
    assert.equal(button('Save details').disabled, true)
    await act(async () => Simulate.change(input('Title'), { target: { value: '私の曲' } }))
    assert.match(document.getElementById(input('Title').getAttribute('aria-describedby')).textContent, /Your edit \(unsaved\)/)
    await act(async () => Simulate.change(input('Genre'), { target: { value: '' } }))
    await act(async () => Simulate.submit(host.querySelector('.manager-editor')))
    assert.deepEqual(saved, [['one', { title: '私の曲', genre: '' }]])
    await act(async () => button('Edit details').click())
    await act(async () => Simulate.change(input('Album'), { target: { value: 'Unwanted edit' } }))
    await act(async () => button('Cancel').click())
    assert.equal(saved.length, 1)
    await act(async () => button('Edit details').click())
    assert.equal(input('Album').value, 'My album')
    assert.equal(button('Save details').disabled, true)
  } finally { Object.assign(api, original); await act(async () => root.unmount()); dom.window.close() }
})
