import { readFileSync } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const projectRoot = path.resolve(webRoot, "..")
const pyproject = readFileSync(path.join(projectRoot, "pyproject.toml"), "utf8")
const version = pyproject.match(/^version\s*=\s*"(\d+\.\d+\.\d+)"\s*$/m)?.[1]
if (!version) throw new Error("无法从 pyproject.toml 读取 MPP 版本号")

const gradle = readFileSync(path.join(webRoot, "android", "app", "build.gradle"), "utf8")
if (!gradle.includes("pyproject.toml") || !gradle.includes("versionName mppVersionName")) {
  throw new Error("Android 构建尚未使用 pyproject.toml 版本号")
}

const release = JSON.parse(readFileSync(path.join(webRoot, "release.config.json"), "utf8"))
const capacitor = readFileSync(path.join(webRoot, "capacitor.config.ts"), "utf8")
const vite = readFileSync(path.join(webRoot, "vite.config.ts"), "utf8")
if (release.appName !== "MPP" || release.appId !== "com.mpp.remote") {
  throw new Error("Android 发布身份配置不符合 MPP 兼容升级要求")
}
if (!capacitor.includes("release.appId") || !gradle.includes("releaseConfig.appId")) {
  throw new Error("Capacitor 与 Gradle 尚未共享 release.config.json")
}
if (!vite.includes("releaseConfig.themeColor") || !gradle.includes("releaseConfig.themeColor")) {
  throw new Error("PWA 与 Android 尚未共享 release.config.json 主题色")
}

process.stdout.write(`${release.appName} ${version} · Android ${release.appId}\n`)
