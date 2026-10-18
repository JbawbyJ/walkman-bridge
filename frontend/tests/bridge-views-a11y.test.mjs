import { register } from 'node:module'

await register(new URL('./jsx-loader.mjs', import.meta.url))
await import('./bridge-views-a11y.suite.jsx')
