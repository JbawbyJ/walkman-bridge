import { createElement } from 'react'
import { artworkUrl } from './mediaImport.js'

// Listening deck and transfer-row thumbnails share one square frame.
// The cleared JPEG is already bounded to 512px on its long side, so these
// CSS sizes stay sharp on high-DPI displays without a second resampled URL.
export const COVER_FRAME = Object.freeze({
  deck: 118,
  thumb: 40,
})

const label = value => typeof value === 'string' ? value.trim().replace(/\s+/g, ' ') : ''

export function coverAltText(item) {
  const name = [item?.album, item?.title, item?.name].map(label).find(Boolean)
  return name ? `Cover for ${name.slice(0, 180)}` : 'Album cover'
}

export function coverFrame(variant = 'deck') {
  const size = COVER_FRAME[variant] ?? COVER_FRAME.thumb
  return { width: size, height: size, aspectRatio: '1 / 1' }
}

export function coverPresentation(item, { variant = 'deck', decorative = false, failedUrl = null } = {}) {
  const source = artworkUrl(item)
  const showImage = Boolean(source) && source !== failedUrl
  const frame = coverFrame(variant)
  return {
    showImage,
    frame,
    src: showImage ? source : null,
    alt: showImage && variant !== 'thumb' && !decorative ? coverAltText(item) : '',
    loading: variant === 'thumb' ? 'lazy' : 'eager',
    decoding: 'async',
    fit: showImage ? 'cover' : 'contain',
  }
}

export function coverClassName(presentation, variant = 'deck') {
  return ['cover-art', `cover-art-${variant}`, presentation.showImage ? 'has-cover' : 'is-fallback', variant === 'deck' ? 'record-art' : '']
    .filter(Boolean).join(' ')
}

export function CoverArtView({ item, variant = 'deck', decorative = false, failedUrl = null, shown = false, lotusSrc, onError, onLoad }) {
  const presentation = coverPresentation(item, { variant, decorative, failedUrl })
  const { frame } = presentation
  const image = presentation.showImage
    ? createElement('img', {
      className: `cover-media${shown ? ' is-shown' : ''}`,
      src: presentation.src,
      alt: presentation.alt,
      width: frame.width,
      height: frame.height,
      loading: presentation.loading,
      decoding: presentation.decoding,
      onError,
      onLoad,
    })
    : createElement('img', {
      className: 'cover-media cover-fallback',
      src: lotusSrc,
      alt: '',
      width: frame.width,
      height: frame.height,
      loading: presentation.loading,
      decoding: 'async',
    })
  return createElement('div', {
    className: coverClassName(presentation, variant),
    'data-cover-frame': String(frame.width),
    'data-cover-state': presentation.showImage ? 'image' : 'fallback',
    style: { aspectRatio: frame.aspectRatio },
  }, image, !presentation.showImage && variant === 'deck' ? createElement('span', null, 'RED LOTUS') : null)
}
