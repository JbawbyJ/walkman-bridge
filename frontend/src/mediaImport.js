import { playable } from './playback.js'

const LINK_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com'])

// This is form feedback only. The backend owns provider, redirect, and network
// validation and never asks the renderer to contact a provider directly.
export function linkImportUrl(input) {
  const value = typeof input === 'string' ? input.trim() : ''
  let url
  try { url = new URL(value) } catch { throw new Error('Enter a YouTube or SoundCloud HTTPS link to one track.') }
  if (value.length > 2048 || /[\u0000-\u0020\u007f]/.test(value) || url.protocol !== 'https:' || url.username || url.password || url.port || !LINK_HOSTS.has(url.hostname)) throw new Error('Use a YouTube or SoundCloud HTTPS link without a login or custom port.')
  return value
}

export function artworkUrl(item) {
  if (!playable(item) || typeof item.id !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(item.id)) return null
  const endpoint = `/api/media/${encodeURIComponent(item.id)}/artwork`
  return item.artwork_url === endpoint ? endpoint : null
}

export function supplementalMetadata(item) {
  const bounded = value => ['string', 'number'].includes(typeof value) ? String(value).slice(0, 120).trim() : ''
  const genre = bounded(item?.genre), year = bounded(item?.year), track = bounded(item?.track)
  return [genre, year, track && `Track ${track}`].filter(Boolean).join(' · ')
}
