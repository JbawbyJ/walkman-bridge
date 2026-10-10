// Run with Electron, not plain Node:
// node_modules/electron/dist/electron.exe frontend/tests/dsp-offline.cjs
// Uses production dsp.js unchanged and native OfflineAudioContext nodes. Only
// createMediaElementSource is adapted to a generated AudioBufferSourceNode.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const assert = require('node:assert/strict')

const output = path.resolve(__dirname, '../test-output/audio')
fs.mkdirSync(output, { recursive: true })
app.setPath('userData', path.join(output, 'electron-profile'))
app.disableHardwareAcceleration()
const sourcePath = path.resolve(__dirname, '../src/dsp.js')
const source = fs.readFileSync(sourcePath)
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
const sourceHash = sha256(source)
const timeout = setTimeout(() => {
  fs.writeFileSync(path.join(output, 'dsp-offline-failure.json'), JSON.stringify({ ok: false, error: 'Offline DSP test timed out after 60 seconds' }, null, 2))
  app.exit(1)
}, 60000)

async function renderTests(moduleUrl) {
  const { createAudioGraph, BAND_FREQUENCIES, PRESETS } = await import(moduleUrl)
  const sampleRate = 48000, tests = []
  const check = (name, passed, metrics) => tests.push({ name, passed, ...metrics })
  const sine = (frequency, amplitude = 0.1, seconds = 2, leadSeconds = 0) => Float32Array.from(
    { length: sampleRate * seconds }, (_, i) => i < sampleRate * leadSeconds ? 0 : amplitude * Math.sin(2 * Math.PI * frequency * i / sampleRate))
  const rms = (samples, start = 0) => Math.sqrt(samples.subarray(start).reduce((sum, sample) => sum + sample * sample, 0) / (samples.length - start))
  const peak = samples => samples.reduce((maximum, sample) => Math.max(maximum, Math.abs(sample)), 0)
  const difference = (left, right) => left.reduce((maximum, sample, i) => Math.max(maximum, Math.abs(sample - right[i])), 0)
  const gainDb = (outputSamples, inputSamples, start = sampleRate) => 20 * Math.log10(rms(outputSamples, start) / rms(inputSamples, start))
  const nativeTypes = new Set()

  async function render(channels, settings = {}) {
    let sourceNode
    class FixtureContext extends OfflineAudioContext {
      constructor() {
        super(channels.length, channels[0].length, sampleRate)
        const buffer = this.createBuffer(channels.length, channels[0].length, sampleRate)
        channels.forEach((samples, channel) => buffer.copyToChannel(samples, channel))
        sourceNode = this.createBufferSource()
        sourceNode.buffer = buffer
      }
      createMediaElementSource() { return sourceNode }
      createBiquadFilter() { const node = super.createBiquadFilter(); nativeTypes.add(node.constructor.name); return node }
      createDynamicsCompressor() { const node = super.createDynamicsCompressor(); nativeTypes.add(node.constructor.name); return node }
      createWaveShaper() { const node = super.createWaveShaper(); nativeTypes.add(node.constructor.name); return node }
    }
    const graph = createAudioGraph(null, FixtureContext)
    graph.update(settings)
    sourceNode.start(0)
    const rendered = await graph.context.startRendering()
    return Array.from({ length: rendered.numberOfChannels }, (_, channel) => new Float32Array(rendered.getChannelData(channel)))
  }

  let randomState = 0x51a7c0de
  const random = () => { randomState ^= randomState << 13; randomState ^= randomState >>> 17; randomState ^= randomState << 5; return (randomState >>> 0) / 0x100000000 * 2 - 1 }
  const probe = [0, 1].map(channel => Float32Array.from({ length: sampleRate * 2 }, (_, i) =>
    0.18 * Math.sin(2 * Math.PI * (channel ? 997 : 431) * i / sampleRate) + 0.11 * Math.sin(2 * Math.PI * 73 * i / sampleRate) + 0.025 * random()))
  const bypassed = await render(probe)
  const bypassError = Math.max(...probe.map((samples, channel) => difference(samples, bypassed[channel])))
  check('Default bypass preserves stereo samples', bypassError <= 1e-7, { maximum_absolute_sample_error: bypassError, tolerance: 1e-7, samples_checked: probe[0].length * 2 })
  const configuredBypass = await render(probe, { enabled: false, gains: Array(10).fill(6), loudness: true, integratedLufs: -6, limiter: true })
  const configuredError = Math.max(...probe.map((samples, channel) => difference(samples, configuredBypass[channel])))
  check('Bypass remains transparent with EQ, matching and limiter configured', configuredError <= 1e-7, { maximum_absolute_sample_error: configuredError, tolerance: 1e-7 })

  const centers = []
  for (let band = 0; band < BAND_FREQUENCIES.length; band++) {
    const frequency = BAND_FREQUENCIES[band], input = sine(frequency)
    const gains = Array(10).fill(0); gains[band] = 6
    const [processed] = await render([input], { enabled: true, gains })
    const measured = gainDb(processed, input)
    centers.push({ frequency_hz: frequency, eq_boost_db: 6, headroom_db: -6, expected_net_gain_db: 0, measured_net_gain_db: measured })
  }
  check('All ten native peaking centers provide +6 dB with -6 dB headroom', centers.every(row => Math.abs(row.measured_net_gain_db) <= 0.08), { tolerance_db: 0.08, measurement_window_seconds: [1, 2], bands: centers })
  // A bypassed graph would also measure 0 dB at a center. This separate distant
  // probe must show the preamp attenuation, proving the EQ/headroom path ran.
  const distantInput = sine(12000), oneBand = Array(10).fill(0); oneBand[5] = 6
  const [distantOutput] = await render([distantInput], { enabled: true, gains: oneBand })
  const distantGain = gainDb(distantOutput, distantInput)
  check('Native EQ headroom attenuates frequencies far from the boosted band', Math.abs(distantGain + 6) <= 0.15, { boosted_band_hz: 1000, probe_hz: 12000, expected_approximately_db: -6, measured_db: distantGain, tolerance_db: 0.15 })
  const loudnessInput = sine(1000)
  const [matched] = await render([loudnessInput], { enabled: true, loudness: true, integratedLufs: -9 })
  const matchingGain = gainDb(matched, loudnessInput)
  check('Native matching applies the requested conservative loudness trim', Math.abs(matchingGain + 9) <= 0.03, { input_lufs_fixture: -9, expected_trim_db: -9, measured_trim_db: matchingGain, tolerance_db: 0.03 })

  // Silence lets the intentional 15 ms dry/wet activation fade settle before
  // the overload begins. The claim is a settled-path sample ceiling, not an
  // instantaneous ceiling while the user is crossfading out of bypass.
  const overload = sine(997, 2.4, 2, 0.5)
  overload[Math.floor(sampleRate * 1.2)] = 4
  overload[Math.floor(sampleRate * 1.4)] = -4
  const [unlimited] = await render([overload], { enabled: true })
  const [limited] = await render([overload], { enabled: true, limiter: true })
  const unlimitedPeak = peak(unlimited), limitedPeak = peak(limited)
  check('Optional limiter bounds settled output sample peaks during overload', unlimitedPeak > 3.9 && limitedPeak <= 0.95001 && limitedPeak > 0.5 && limited.every(Number.isFinite), {
    input_peak: peak(overload), limiter_disabled_peak: unlimitedPeak, limiter_enabled_peak: limitedPeak, ceiling: 0.95, tolerance: 0.00001, initial_silence_seconds: 0.5,
  })

  const seconds = 6, start = 0.5, notes = [220, 261.6256, 329.6276, 392, 440, 392, 329.6276, 261.6256]
  const comparison = [new Float32Array(sampleRate * seconds), new Float32Array(sampleRate * seconds)]
  for (let i = 0; i < comparison[0].length; i++) {
    const t = i / sampleRate - start
    if (t < 0) continue
    const beat = Math.floor(t / 0.375), age = t % 0.375, note = notes[beat % notes.length]
    const envelope = Math.min(1, t / 0.05) * Math.min(1, (seconds - start - t) / 0.3)
    const bass = 0.105 * Math.sin(2 * Math.PI * 55 * t) * Math.exp(-age * 5)
    const pluck = 0.16 * (Math.sin(2 * Math.PI * note * t) + 0.25 * Math.sin(2 * Math.PI * 2 * note * t)) * Math.exp(-age * 8)
    const tick = 0.018 * random() * Math.exp(-(t % 0.1875) * 90)
    comparison[0][i] = envelope * (bass + pluck + tick + 0.045 * Math.sin(2 * Math.PI * 164.8138 * t))
    comparison[1][i] = envelope * (bass + 0.9 * pluck + tick + 0.045 * Math.sin(2 * Math.PI * 220 * t + 0.25))
  }
  const comparisonSettings = { enabled: true, gains: PRESETS.Warm, loudness: false, limiter: true }
  const enhanced = await render(comparison, comparisonSettings)
  const encodeFloatChannels = channels => channels.map(samples => {
    const bytes = new Uint8Array(samples.buffer), chunks = []
    for (let i = 0; i < bytes.length; i += 0x8000) chunks.push(String.fromCharCode(...bytes.subarray(i, i + 0x8000)))
    return btoa(chunks.join(''))
  })
  return {
    tests, native_node_types: [...nativeTypes], sample_rate_hz: sampleRate,
    comparison: { duration_seconds: seconds, channels: 2, description: 'Original procedurally composed stereo tones, plucks, bass and noise ticks', settings: comparisonSettings, post_normalization: false, original_peak: Math.max(...comparison.map(peak)), enhanced_peak: Math.max(...enhanced.map(peak)), original: encodeFloatChannels(comparison), enhanced: encodeFloatChannels(enhanced) },
  }
}

function pcm24Wave(base64Channels, sampleRate) {
  const channels = base64Channels.map(channel => Buffer.from(channel, 'base64'))
  const frames = channels[0].length / 4, blockAlign = channels.length * 3
  const wave = Buffer.alloc(44 + frames * blockAlign)
  wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8)
  wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(channels.length, 22)
  wave.writeUInt32LE(sampleRate, 24); wave.writeUInt32LE(sampleRate * blockAlign, 28)
  wave.writeUInt16LE(blockAlign, 32); wave.writeUInt16LE(24, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
  for (let frame = 0; frame < frames; frame++) for (let channel = 0; channel < channels.length; channel++) {
    const sample = channels[channel].readFloatLE(frame * 4)
    assert.ok(Number.isFinite(sample) && Math.abs(sample) <= 1, 'comparison export must not clip')
    wave.writeIntLE(Math.max(-8388608, Math.min(8388607, Math.round(sample * 8388607))), 44 + frame * blockAlign + channel * 3, 3)
  }
  return wave
}

async function main() {
  await app.whenReady()
  const win = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false } })
  await win.loadURL('data:text/html;charset=utf-8,%3Ctitle%3EOffline%20DSP%20verification%3C%2Ftitle%3E')
  const moduleUrl = `data:text/javascript;base64,${source.toString('base64')}`
  const results = await win.webContents.executeJavaScript(`(${renderTests.toString()})(${JSON.stringify(moduleUrl)})`)
  const original = pcm24Wave(results.comparison.original, results.sample_rate_hz)
  const enhanced = pcm24Wave(results.comparison.enhanced, results.sample_rate_hz)
  fs.writeFileSync(path.join(output, 'original.wav'), original)
  fs.writeFileSync(path.join(output, 'enhanced.wav'), enhanced)
  delete results.comparison.original; delete results.comparison.enhanced
  assert.equal(sha256(fs.readFileSync(sourcePath)), sourceHash, 'production DSP source changed during the test')
  const report = {
    ok: results.tests.every(result => result.passed), generated_at: new Date().toISOString(),
    runtime: { electron: process.versions.electron, chromium: process.versions.chrome, node: process.versions.node },
    source: { path: 'frontend/src/dsp.js', sha256: sourceHash },
    method: 'Unmodified production createAudioGraph using native Electron OfflineAudioContext; only the media-element source is adapted to a generated AudioBufferSourceNode',
    ...results,
    artifacts: [ { path: 'original.wav', sha256: sha256(original), bytes: original.length }, { path: 'enhanced.wav', sha256: sha256(enhanced), bytes: enhanced.length } ],
    limitations: ['No human listening assessment was performed.', 'Limiter assertion covers sample peaks after the intentional bypass activation fade, not inter-sample true peaks or instantaneous switching.', 'WAV comparison uses 24-bit PCM and conservative Warm headroom without loudness normalization; measurements use the raw floating-point renders.'],
  }
  fs.writeFileSync(path.join(output, 'dsp-offline.json'), JSON.stringify(report, null, 2))
  win.destroy()
  assert.ok(report.ok, `DSP assertions failed: ${report.tests.filter(test => !test.passed).map(test => test.name).join(', ')}`)
  fs.rmSync(path.join(output, 'dsp-offline-failure.json'), { force: true })
  console.log(`Offline DSP PASS: ${report.tests.length} native audio checks; comparison WAVs and JSON in ${output}`)
}

main().then(() => { clearTimeout(timeout); app.exit(0) }).catch(error => {
  fs.writeFileSync(path.join(output, 'dsp-offline-failure.json'), JSON.stringify({ ok: false, generated_at: new Date().toISOString(), source_sha256: sourceHash, error: error.stack || String(error) }, null, 2))
  console.error(error); clearTimeout(timeout); app.exit(1)
})
