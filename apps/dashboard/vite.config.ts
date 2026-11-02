import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // WP6's dashboard server serves dist at '/': relative asset paths work wherever it is mounted.
  base: './',
  plugins: [react()],
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
