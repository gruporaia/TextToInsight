import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em dev (`npm run dev`), o Vite serve o front em :5173 e faz proxy de /api para o
// backend FastAPI em :8000. Em producao, `npm run build` gera dist/, servido pelo
// proprio FastAPI -- por isso `base: "./"` (caminhos relativos).
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
