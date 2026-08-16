import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Tailwind v4 is a Vite plugin and is configured in CSS (src/index.css), not in a
// tailwind.config.js — see docs/decisions.md D-029.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
