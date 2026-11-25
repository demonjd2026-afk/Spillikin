import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// A relative base lets the same build run at a domain root or under a GitHub Pages project path.
export default defineConfig({ base: './', plugins: [react(), tailwindcss()] });
