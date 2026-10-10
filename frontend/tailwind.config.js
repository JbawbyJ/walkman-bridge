/** @type {import('tailwindcss').Config} */
//
// Theme = the Red Lotus tokens declared in src/index.css. Every color here is
// a CSS variable so the design system stays the single source of truth; add a
// new token there first, then expose it here. (No opacity modifiers on these
// — `bg-brand/20` cannot resolve a var() color.)
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Orbitron"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        body: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        page: 'var(--color-bg-page)',
        surface: 'var(--color-bg-surface)',
        sunken: 'var(--color-bg-sunken)',
        ink: 'var(--color-text-primary)',
        ink2: 'var(--color-text-secondary)',
        muted: 'var(--color-text-muted)',
        'ink-inverse': 'var(--color-text-inverse)',
        brand: 'var(--color-brand)',
        'brand-text': 'var(--color-text-brand)',
        'brand-soft': 'var(--color-brand-soft)',
        line: 'var(--color-border-default)',
        'line-strong': 'var(--color-border-strong)',
        'line-brand': 'var(--color-border-brand)',
        success: 'var(--color-success)',
        warning: 'var(--color-warning)',
        danger: 'var(--color-danger)',
        info: 'var(--color-info)',
        idle: 'var(--rl-gray-500)',
      },
      letterSpacing: {
        // Red Lotus tracking scale (overrides Tailwind's defaults for these names)
        wide: '0.04em',
        wider: '0.12em',
        widest: '0.22em',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
    },
  },
  plugins: [],
}
