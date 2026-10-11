import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../package.json', import.meta.url))
const esbuild = require('esbuild')

const asset = new Set(['.png', '.css', '.woff2', '.svg'])

export async function load(url, context, nextLoad) {
  if (url.startsWith('data:')) return nextLoad(url, context)
  const path = url.split('?')[0]
  const extension = path.slice(path.lastIndexOf('.'))
  if (asset.has(extension)) return { format: 'module', source: 'export default "asset"', shortCircuit: true }
  if (extension === '.jsx') {
    const source = readFileSync(fileURLToPath(path), 'utf8')
    const { code } = esbuild.transformSync(source, { loader: 'jsx', format: 'esm', jsx: 'automatic', sourcefile: path })
    return { format: 'module', source: code, shortCircuit: true }
  }
  return nextLoad(url, context)
}
