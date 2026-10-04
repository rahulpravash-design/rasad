import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI calls /api/*; Vite forwards it to the FastAPI server on :8000 and strips the prefix.
// In Docker, nginx does the same (ui/nginx.conf).
const proxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  // The MapLibre worker is built as an ES module (see src/components/SectorMap.tsx).
  worker: { format: "es" },
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
