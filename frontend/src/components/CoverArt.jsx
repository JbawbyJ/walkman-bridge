import { useState } from 'react'
import lotus from '../assets/lotus.png'
import { CoverArtView } from '../coverArt.js'
import { artworkUrl } from '../mediaImport.js'

export function CoverArt({ item, variant = 'deck' }) {
  const source = artworkUrl(item)
  return <CoverArtLoader key={source || `${variant}:fallback`} item={item} variant={variant} source={source} />
}

function CoverArtLoader({ item, variant, source }) {
  const [failedUrl, setFailedUrl] = useState(null)
  const [shown, setShown] = useState(false)
  return <CoverArtView item={item} variant={variant} failedUrl={failedUrl} shown={shown} lotusSrc={lotus} onError={() => setFailedUrl(source)} onLoad={() => setShown(true)} />
}
