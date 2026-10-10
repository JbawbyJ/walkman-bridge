import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextTrackId, moveTrack, restoreSession, playable } from './playback.js'
import { BAND_FREQUENCIES, PRESETS, dspSettings, peakingCoefficients, filterSignal } from './dsp.js'

const ready = id => ({ id, status: 'ready', scan: { ok: true } })
const queue = [ready('a'), { id: 'blocked', status: 'blocked' }, ready('b'), ready('c')]

test('queue advances only cleared tracks and stops at the end unless repeat all', () => {
  assert.equal(nextTrackId(queue, 'a'), 'b')
  assert.equal(nextTrackId(queue, 'c'), null)
  assert.equal(nextTrackId(queue, 'c', { repeat: 'all' }), 'a')
  assert.equal(nextTrackId(queue, 'b', { repeat: 'one' }), 'b')
  assert.equal(nextTrackId(queue, 'b', { direction: -1 }), 'a')
  assert.equal(playable({ status: 'ready', scan: { ok: false } }), false)
})

test('shuffle excludes the current track and never selects uncleared records', () => {
  for (const random of [() => 0, () => 0.999]) {
    assert.ok(['b', 'c'].includes(nextTrackId(queue, 'a', { shuffle: true, random })))
  }
  assert.equal(nextTrackId([ready('a')], 'a', { shuffle: true }), null)
  assert.equal(nextTrackId([], 'a', { shuffle: true }), null)
})

test('queue reorder keeps an exact immutable permutation', () => {
  assert.deepEqual(moveTrack(queue, 'b', -1).map(x => x.id), ['a', 'b', 'blocked', 'c'])
  assert.deepEqual(moveTrack(queue, 'a', -1), queue)
  assert.deepEqual(queue.map(x => x.id), ['a', 'blocked', 'b', 'c'])
})

test('session parsing is bounded and never restores playing', () => {
  assert.deepEqual(restoreSession({ id: 'a', position: -30, volume: 4, playing: true, repeat: 'bogus' }),
    { id: 'a', position: 0, volume: 1, shuffle: false, repeat: 'off', playing: false })
  assert.equal(restoreSession({ position: Infinity }).position, 0)
  assert.equal(restoreSession(null).playing, false)
})

test('DSP is transparent by default and has ten conservative bands', () => {
  assert.equal(BAND_FREQUENCIES.length, 10)
  assert.equal(dspSettings().enabled, false)
  assert.equal(dspSettings().limiter, false)
  assert.equal(dspSettings().preampDb, 0)
  for (const gains of Object.values(PRESETS)) assert.ok(gains.every(x => Math.abs(x) <= 3))
  assert.ok(dspSettings({ enabled: true, gains: Array(10).fill(6) }).preampDb <= -6)
})

test('synthetic 1 kHz signal gains 6 dB at its peaking center and flat EQ preserves samples', () => {
  const rate = 48000
  const signal = Float64Array.from({ length: rate }, (_, i) => 0.1 * Math.sin(2 * Math.PI * 1000 * i / rate))
  const rms = data => Math.sqrt(data.slice(rate / 2).reduce((sum, x) => sum + x * x, 0) / (rate / 2))
  const boosted = filterSignal(signal, peakingCoefficients(1000, 6, rate))
  assert.ok(Math.abs(20 * Math.log10(rms(boosted) / rms(signal)) - 6) < 0.03)
  const flat = filterSignal(signal, peakingCoefficients(1000, 0, rate))
  assert.ok(flat.every((x, i) => Math.abs(x - signal[i]) < 1e-10))
})

test('high frequency coefficients remain stable at low sample rates', () => {
  const impulse = new Float64Array(16000)
  impulse[0] = 0.1
  for (const frequency of BAND_FREQUENCIES) {
    const output = filterSignal(impulse, peakingCoefficients(frequency, 6, 16000))
    assert.ok(output.every(Number.isFinite))
    assert.ok(Math.abs(output.at(-1)) < 0.000001)
  }
})
