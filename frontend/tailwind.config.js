/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        body: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        bg: '#0a0a0a',
        panel: '#141414',
        line: '#262626',
        ink: '#e8e6e1',
        muted: '#6b6b6b',
        amber: '#ffb627',
        cyan: '#5ad7e8',
        crimson: '#e84545',
      },
    },
  },
  plugins: [],
}
