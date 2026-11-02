/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          950: '#090d16',
          900: '#0f172a',
          850: '#151f32',
          800: '#1e293b',
          700: '#334155'
        },
        brand: {
          500: '#3b82f6',
          600: '#2563eb'
        }
      }
    }
  },
  plugins: [],
}
