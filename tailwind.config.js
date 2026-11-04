/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
    './static/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          950: '#0f1012', // Base dark neutral background
          900: '#16171b', // Cards & Sidebar
          850: '#1f2025', // Secondary container surfaces
          800: '#282930', // Inputs & hover surfaces
          750: '#32343d', // Primary borders
          700: '#3e414d'  // Lighter borders
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
