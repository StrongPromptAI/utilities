/** @type {import('tailwindcss').Config} */
// Ported from the THJ pt-assistant frontend so the coach widget shares the exact
// design-token system (rgb(var(--token) / <alpha>)). Token VALUES live in src/index.css.
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: 'rgb(var(--primary) / <alpha-value>)',
          600: 'rgb(var(--primary-600) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          light: 'rgb(var(--accent-light) / <alpha-value>)',
        },
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          alt: 'rgb(var(--surface-alt) / <alpha-value>)',
          dim: 'rgb(var(--surface-dim) / <alpha-value>)',
          input: 'rgb(var(--surface-input) / <alpha-value>)',
        },
        page: {
          DEFAULT: 'rgb(var(--page) / <alpha-value>)',
          end: 'rgb(var(--page-end) / <alpha-value>)',
        },
        text: {
          DEFAULT: 'rgb(var(--text) / <alpha-value>)',
          secondary: 'rgb(var(--text-secondary) / <alpha-value>)',
          muted: 'rgb(var(--text-muted) / <alpha-value>)',
        },
      },
    },
  },
  plugins: [],
}
