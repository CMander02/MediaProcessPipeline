import path from "path"
import vue from "@vitejs/plugin-vue"
import UnoCSS from "unocss/vite"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [vue(), UnoCSS()],
  base: "./",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
})
