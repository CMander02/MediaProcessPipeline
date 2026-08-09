import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"
import releaseConfig from "./release.config.json"

const releaseConfigPlugin = {
  name: "mpp-release-config",
  transformIndexHtml(html: string) {
    return html
      .replaceAll("__MPP_APP_NAME__", releaseConfig.appName)
      .replaceAll("__MPP_THEME_COLOR__", releaseConfig.themeColor)
  },
}

export default defineConfig({
  plugins: [
    releaseConfigPlugin,
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "prompt",
      injectRegister: false,
      manifest: {
        id: "/",
        name: `${releaseConfig.appName} · Media Process Pipeline`,
        short_name: releaseConfig.appName,
        description: "将音视频转化为结构化知识",
        lang: "zh-CN",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: releaseConfig.themeColor,
        icons: [
          { src: "/pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512x512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{html,js,css,woff,woff2,png,svg}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/health$/],
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
      },
      devOptions: { enabled: false },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:18000",
      "/health": "http://localhost:18000",
    },
  },
})
