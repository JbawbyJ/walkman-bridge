import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { CoverArtView, coverPresentation } from './coverArt.js'

const ready = {
  id: 'one',
  status: 'ready',
  scan: { ok: true },
  title: 'Blue in Green',
  artist: 'Bill Evans',
  album: 'Kind of Blue',
  name: 'blue.flac',
  artwork_url: '/api/media/one/artwork',
}

const markup = props => renderToStaticMarkup(createElement(CoverArtView, { lotusSrc: '/lotus.png', ...props }))
const frameOf = html => html.match(/data-cover-frame="(\d+)"/)[1]

test('missing artwork and a failed load use the lotus fallback in the same square frame', () => {
  for (const variant of ['deck', 'thumb']) {
    const missing = coverPresentation(null, { variant })
    const blocked = coverPresentation({ ...ready, artwork_url: 'https://example.com/cover.jpg' }, { variant })
    const failed = coverPresentation(ready, { variant, failedUrl: ready.artwork_url })
    for (const presentation of [missing, blocked, failed]) {
      assert.equal(presentation.showImage, false)
      assert.equal(presentation.src, null)
      assert.equal(presentation.alt, '')
      assert.equal(presentation.fit, 'contain')
      assert.equal(presentation.frame.width, presentation.frame.height)
      assert.equal(presentation.frame.aspectRatio, '1 / 1')
    }
    assert.equal(missing.frame.width, failed.frame.width)
    assert.equal(missing.frame.height, failed.frame.height)

    const failedMarkup = markup({ item: ready, variant, failedUrl: ready.artwork_url })
    const missingMarkup = markup({ item: null, variant })
    assert.equal(frameOf(failedMarkup), frameOf(missingMarkup))
    assert.match(failedMarkup, /data-cover-state="fallback"/)
    assert.match(failedMarkup, /src="\/lotus.png"/)
    assert.match(failedMarkup, /alt=""/)
    assert.doesNotMatch(failedMarkup, /https:\/\/example.com/)
  }
})

test('deck art names the album and transfer thumbnails stay decorative', () => {
  const deck = coverPresentation(ready, { variant: 'deck' })
  const staged = coverPresentation(ready, { variant: 'thumb' })
  assert.equal(deck.showImage, true)
  assert.equal(deck.alt, 'Cover for Kind of Blue')
  assert.equal(deck.src, '/api/media/one/artwork')
  assert.equal(staged.alt, '')
  assert.equal(coverPresentation({ ...ready, album: '  ', title: 'Blue in Green' }, { variant: 'deck' }).alt, 'Cover for Blue in Green')
  assert.equal(coverPresentation({ ...ready, album: '', title: '', name: '' }, { variant: 'deck' }).alt, 'Album cover')

  const deckMarkup = markup({ item: ready, variant: 'deck' })
  const thumbMarkup = markup({ item: ready, variant: 'thumb' })
  assert.match(deckMarkup, /alt="Cover for Kind of Blue"/)
  assert.match(thumbMarkup, /<img[^>]*alt=""/)
  assert.equal(frameOf(deckMarkup), String(deck.frame.width))
  assert.equal(frameOf(thumbMarkup), String(staged.frame.width))
  assert.equal(deck.frame.width, deck.frame.height)
  assert.equal(staged.frame.width, staged.frame.height)
  assert.notEqual(deck.frame.width, staged.frame.width)
})

test('a cover and its fallback reserve the same placeholder, and list art is lazy', () => {
  const shown = coverPresentation(ready, { variant: 'thumb' })
  const fallback = coverPresentation(ready, { variant: 'thumb', failedUrl: ready.artwork_url })
  assert.deepEqual(shown.frame, fallback.frame)
  assert.equal(shown.loading, 'lazy')
  assert.equal(shown.decoding, 'async')
  assert.equal(fallback.decoding, 'async')
  assert.equal(coverPresentation(ready, { variant: 'deck' }).loading, 'eager')

  const shownMarkup = markup({ item: ready, variant: 'thumb', shown: true })
  const fallbackMarkup = markup({ item: ready, variant: 'thumb', failedUrl: ready.artwork_url })
  assert.equal(frameOf(shownMarkup), frameOf(fallbackMarkup))
  assert.match(shownMarkup, /loading="lazy"/)
  assert.match(shownMarkup, /decoding="async"/)
  assert.match(fallbackMarkup, /loading="lazy"/)
  assert.match(fallbackMarkup, /decoding="async"/)
  assert.match(shownMarkup, /width="40"/)
  assert.match(shownMarkup, /height="40"/)
  assert.match(fallbackMarkup, /width="40"/)
  assert.match(fallbackMarkup, /height="40"/)
  assert.match(shownMarkup, /aspect-ratio:1 \/ 1/)
  assert.match(fallbackMarkup, /aspect-ratio:1 \/ 1/)

  const deckShown = markup({ item: ready, variant: 'deck' })
  const deckFallback = markup({ item: null, variant: 'deck' })
  assert.equal(frameOf(deckShown), frameOf(deckFallback))
  assert.match(deckShown, /width="118"/)
  assert.match(deckShown, /height="118"/)
  assert.match(deckFallback, /width="118"/)
  assert.match(deckFallback, /height="118"/)
  assert.match(deckShown, /loading="eager"/)
  assert.match(deckShown, /decoding="async"/)
})

test('cover styles keep a square crop, a contained fallback, and no fade when motion is reduced', () => {
  const css = fs.readFileSync(new URL('./cover-art.css', import.meta.url), 'utf8')
  assert.match(css, /\.cover-art\s*\{[^}]*aspect-ratio:\s*1\s*\/\s*1/)
  assert.match(css, /\.cover-art-thumb\s*\{[^}]*width:\s*var\(--cover-size[^}]*height:\s*var\(--cover-size/s)
  assert.match(css, /\.staged-file > \.cover-art-thumb,\s*\.queue-row > \.cover-art-thumb\s*\{[^}]*flex:\s*0 0 var\(--cover-size\)/)
  assert.match(css, /\.cover-art\.has-cover img\.cover-media\s*\{[^}]*object-fit:\s*cover/)
  assert.match(css, /\.cover-art\.is-fallback img\.cover-media\.cover-fallback\s*\{[^}]*object-fit:\s*contain/)
  const motion = css.match(/@media \(prefers-reduced-motion:\s*no-preference\)\s*\{[\s\S]*?\n\}/)
  assert.ok(motion, 'fade lives only in the no-preference motion query')
  assert.match(motion[0], /opacity:\s*0/)
  assert.match(motion[0], /transition:\s*opacity/)
  const rest = css.replace(motion[0], '')
  assert.doesNotMatch(rest, /opacity:\s*0/)
  assert.match(css, /image-rendering:\s*high-quality/)
})

test('listening deck and transfer rows are the only new cover mounts', () => {
  const deck = fs.readFileSync(new URL('./components/PlayerPanels.jsx', import.meta.url), 'utf8')
  const transfer = fs.readFileSync(new URL('./components/BridgePanels.jsx', import.meta.url), 'utf8')
  assert.match(deck, /export function Artwork\(\{ item \}\) \{\s*return <CoverArt item=\{item\} variant="deck" \/>/)
  assert.match(deck, /<CoverArt item=\{item\} variant="thumb" \/>/)
  assert.match(transfer, /<CoverArt item=\{item\} variant="thumb" \/>/)
})
