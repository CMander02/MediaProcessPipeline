import type { CapacitorConfig } from "@capacitor/cli"
import { readFileSync } from "node:fs"
import path from "node:path"

const release = JSON.parse(
  readFileSync(path.resolve(process.cwd(), "release.config.json"), "utf8"),
) as { appId: string; appName: string }

const config: CapacitorConfig = {
  appId: release.appId,
  appName: release.appName,
  webDir: "dist",
  loggingBehavior: "none",
  server: {
    hostname: "localhost",
    androidScheme: "https",
  },
  android: {
    allowMixedContent: true,
  },
  plugins: {
    SystemBars: {
      insetsHandling: "css",
      style: "DEFAULT",
      hidden: false,
    },
  },
}

export default config
