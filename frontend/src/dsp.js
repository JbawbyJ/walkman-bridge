export const BAND_FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
export const PRESETS = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  Warm: [2, 2, 1, 0, 0, 0, -1, -1, 0, 0],
  Presence: [0, 0, -1, -1, 0, 1, 2, 1, 0, 0],
  'Late night': [-2, -1, 0, 1, 1, 1, 0, -1, -2, -2],
}

export function dspSettings(input = {}) {
  const gains = BAND_FREQUENCIES.map((_, i) => Number.isFinite(input.gains?.[i]) ? Math.max(-6, Math.min(6, input.gains[i])) : 0)
  const enabled = input.enabled === true
  const loudness = input.loudness === true
  // Sum positive boosts is a conservative upper bound for this cascade. Never
  // add makeup gain: matching only trims the extra perceived loudness.
  const headroom = gains.reduce((sum, gain) => sum + Math.max(0, gain), 0)
  const integratedLufs = Number.isFinite(input.integratedLufs) ? input.integratedLufs : null
  const matchTrim = loudness && integratedLufs !== null ? Math.max(0, integratedLufs + 18) : 0
  return { enabled, gains, loudness, integratedLufs, limiter: input.limiter === true, preampDb: enabled ? -headroom - matchTrim : 0 }
}

// RBJ peaking biquad, also used for independent synthetic-signal regression
// checks. The runtime uses native Web Audio filters with these same parameters.
export function peakingCoefficients(frequency, gainDb, sampleRate, q = 1.4) {
  const w = 2 * Math.PI * Math.min(frequency, sampleRate * 0.45) / sampleRate
  const a = 10 ** (gainDb / 40)
  const alpha = Math.sin(w) / (2 * q)
  const a0 = 1 + alpha / a
  return { b0: (1 + alpha * a) / a0, b1: -2 * Math.cos(w) / a0, b2: (1 - alpha * a) / a0, a1: -2 * Math.cos(w) / a0, a2: (1 - alpha / a) / a0 }
}

export function filterSignal(input, c) {
  const out = new Float64Array(input.length)
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0
  for (let i = 0; i < input.length; i++) {
    const y = c.b0 * input[i] + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2
    out[i] = y; x2 = x1; x1 = input[i]; y2 = y1; y1 = y
  }
  return out
}

export function createAudioGraph(audio, Context = window.AudioContext || window.webkitAudioContext) {
  if (!Context) throw new Error('Audio processing is unavailable in this runtime.')
  const context = new Context()
  const source = context.createMediaElementSource(audio)
  const analyser = context.createAnalyser()
  analyser.fftSize = 2048
  analyser.smoothingTimeConstant = 0.72
  const preamp = context.createGain()
  const filters = BAND_FREQUENCIES.map(frequency => {
    const filter = context.createBiquadFilter()
    filter.type = 'peaking'
    filter.frequency.value = Math.min(frequency, context.sampleRate * 0.45)
    filter.Q.value = 1.4
    return filter
  })
  const limiter = context.createDynamicsCompressor()
  limiter.threshold.value = -1
  limiter.knee.value = 0
  limiter.ratio.value = 20
  limiter.attack.value = 0.003
  limiter.release.value = 0.12
  const hardLimit = context.createWaveShaper()
  // The compressor controls sustained peaks. This final safety ceiling catches
  // transients inside its attack window when the optional limiter is enabled.
  hardLimit.curve = Float32Array.from({ length: 4097 }, (_, i) => Math.max(-0.95, Math.min(0.95, i / 2048 - 1)))
  const dry = context.createGain(), wet = context.createGain()
  source.connect(dry).connect(analyser)
  source.connect(preamp)
  filters.reduce((previous, filter) => previous.connect(filter), preamp)
  analyser.connect(context.destination)
  let disposed = false
  const update = input => {
    if (disposed) return
    const settings = dspSettings(input)
    const now = context.currentTime
    // These two paths meet before the analyser. A short ramp prevents clicks.
    dry.gain.setTargetAtTime(settings.enabled ? 0 : 1, now, 0.015)
    wet.gain.setTargetAtTime(settings.enabled ? 1 : 0, now, 0.015)
    preamp.gain.setTargetAtTime(10 ** (settings.preampDb / 20), now, 0.015)
    filters.forEach((filter, i) => filter.gain.setTargetAtTime(settings.gains[i], now, 0.015))
    const last = filters.at(-1)
    last.disconnect(); limiter.disconnect(); hardLimit.disconnect(); wet.disconnect()
    if (settings.limiter) last.connect(limiter).connect(hardLimit).connect(wet)
    else last.connect(wet)
    wet.connect(analyser)
  }
  // Wet starts muted: bypass must never double the signal on first connect.
  wet.gain.value = 0
  update({})
  return { context, analyser, update, resume: () => context.resume(), dispose: () => {
    if (disposed) return
    disposed = true
    for (const node of [source, dry, wet, preamp, ...filters, limiter, hardLimit, analyser]) node.disconnect()
    return context.close().catch(() => {})
  } }
}
