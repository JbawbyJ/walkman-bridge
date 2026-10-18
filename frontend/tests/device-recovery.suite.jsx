import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from '../src/api.js'
import DeviceRecoveryPanel from '../src/components/DeviceRecoveryPanel.jsx'

test('inspection never writes; acknowledgement requires explicit review and uses the snapshot token', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' })
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true })) {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
  const original = { ...api }, calls = []
  api.inspectRecoveryJournal = async () => ({ state: 'none', etag: 'journal-etag' })
  api.inspectDeviceState = async () => ({ etag: 'snapshot-etag', can_acknowledge: true,
    tracks: [{ id: '1', title: 'Checked track' }], playlists: [], warnings: [],
    jobs: [{ job_id: 'unknown-job', same_device: true, files: [{ name: 'Interrupted file', state: 'unknown' }] }] })
  api.acknowledgeDeviceState = async (...args) => { calls.push(['ack', ...args]); return { job_id: 'unknown-job' } }
  api.recoverPlaylistJournal = async (...args) => calls.push(['recover', ...args])
  api.repairPlaylists = async (...args) => calls.push(['repair', ...args])
  const host = document.querySelector('#root'), root = createRoot(host)
  const button = label => [...host.querySelectorAll('button')].find(el => el.textContent === label)
  try {
    await act(async () => root.render(<DeviceRecoveryPanel connected busy={false} />))
    assert.deepEqual(calls, [])
    await act(async () => button('Inspect recovery state').click())
    assert.deepEqual(calls, [])
    assert.match(host.textContent, /Checked track/)
    assert.equal(button('Acknowledge inspected state').disabled, true)
    await act(async () => host.querySelector('input[type="checkbox"]').click())
    await act(async () => button('Acknowledge inspected state').click())
    assert.deepEqual(calls, [['ack', 'unknown-job', 'snapshot-etag']])
    assert.match(host.textContent, /No automatic retry/)
    assert.equal(button('Acknowledge inspected state'), undefined)
  } finally { Object.assign(api, original); await act(async () => root.unmount()); dom.window.close() }
})

test('pending recovery requires confirmation, sends journal token and discards stale state after a conflict', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' })
  Object.defineProperty(globalThis, 'window', { value: dom.window, configurable: true })
  Object.defineProperty(globalThis, 'document', { value: dom.window.document, configurable: true })
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const original = { ...api }, calls = []
  api.inspectRecoveryJournal = async () => ({ state: 'committed', guarded: true, etag: 'old-journal', covers: { files: ['01TREE22.DAT'] } })
  api.inspectDeviceState = async () => ({ can_acknowledge: false, tracks: [], playlists: [], jobs: [], warnings: [] })
  api.recoverPlaylistJournal = async etag => { calls.push(etag); throw new Error('Device changed; inspect again') }
  const host = document.querySelector('#root'), root = createRoot(host)
  const button = label => [...host.querySelectorAll('button')].find(el => el.textContent === label)
  try {
    await act(async () => root.render(<DeviceRecoveryPanel connected />))
    await act(async () => button('Inspect recovery state').click())
    assert.equal(button('Recover interrupted write').disabled, true)
    await act(async () => host.querySelector('input[type="checkbox"]').click())
    await act(async () => button('Recover interrupted write').click())
    assert.deepEqual(calls, ['old-journal'])
    assert.match(host.textContent, /Device changed/)
    assert.equal(button('Recover interrupted write'), undefined)
  } finally { Object.assign(api, original); await act(async () => root.unmount()); dom.window.close() }
})
