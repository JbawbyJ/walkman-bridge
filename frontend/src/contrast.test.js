import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

function linear(channel) {
  const value = channel / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

function contrast(foreground, background) {
  const lum = hex => {
    const n = Number.parseInt(hex.slice(1), 16)
    return 0.2126 * linear(n >> 16) + 0.7152 * linear((n >> 8) & 255) + 0.0722 * linear(n & 255)
  }
  const [hi, lo] = [lum(foreground), lum(background)].sort((a, b) => b - a)
  return (hi + 0.05) / (lo + 0.05)
}

test('Listening, Walkman, and Transfer text colors meet WCAG AA on their surfaces', () => {
  const pairs = [
    ['#f0e5d5', '#100c0e', 4.5],
    ['#eddbc7', '#171315', 4.5],
    ['#e3cdb8', '#171315', 4.5],
    ['#ffcabd', '#401c1d', 4.5],
    ['#ffe4dc', '#401c1d', 4.5],
    ['#20140c', '#d77a33', 4.5],
    ['#20140c', '#ffd089', 4.5],
    ['#ffd089', '#100c0e', 3],
    ['#b08978', '#171315', 3],
    ['#b08978', '#241a17', 3],
  ]
  for (const [foreground, background, minimum] of pairs) {
    const ratio = contrast(foreground, background)
    assert.ok(ratio >= minimum, `${foreground} on ${background} is ${ratio.toFixed(2)}, need ${minimum}`)
  }
})

test('bridge styles keep a visible focus ring, reduced motion, and a meter boundary', () => {
  const night = fs.readFileSync(new URL('../src/nightops.css', import.meta.url), 'utf8')
  const retro = fs.readFileSync(new URL('../src/retro.css', import.meta.url), 'utf8')
  assert.match(night, /button:focus-visible/)
  assert.match(night, /a:focus-visible/)
  assert.match(night, /\[role="slider"\]:focus-visible/)
  assert.match(night, /\.storage-meter\{[^}]*#b08978/)
  assert.match(night, /\.skip-link/)
  assert.match(night, /\.visually-hidden/)
  assert.match(retro, /@media\(prefers-reduced-motion:reduce\)/)
  assert.match(retro, /animation:none!important/)
})
