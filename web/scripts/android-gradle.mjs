import { spawnSync } from "node:child_process"
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(scriptDir, "..")
const androidRoot = path.join(webRoot, "android")
const tasks = process.argv.slice(2)

if (!existsSync(androidRoot)) {
  throw new Error("尚未生成 Capacitor Android 工程，请先运行 npm run android:add。")
}
if (tasks.length === 0 || tasks.some((task) => !/^[A-Za-z][A-Za-z0-9:_-]*$/.test(task))) {
  throw new Error("请提供有效的 Gradle 任务。")
}

function javaMajorVersion(javaHome) {
  if (!javaHome) return null
  const releaseFile = path.join(javaHome, "release")
  if (!existsSync(releaseFile)) return null
  const match = readFileSync(releaseFile, "utf8").match(/^JAVA_VERSION="(\d+)/m)
  return match ? Number(match[1]) : null
}

const env = { ...process.env }
if (process.platform === "win32") {
  env.JAVA_HOME ||= "C:\\Program Files\\Android\\Android Studio\\jbr"
  env.ANDROID_HOME ||= path.join(env.LOCALAPPDATA ?? "", "Android", "Sdk")
  env.ANDROID_SDK_ROOT ||= env.ANDROID_HOME
} else if (process.platform === "darwin") {
  const javaCandidates = [
    env.MPP_ANDROID_JAVA_HOME,
    "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
    "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
    "/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
  ].filter(Boolean)
  const currentMajor = javaMajorVersion(env.JAVA_HOME)
  if (!env.JAVA_HOME || (currentMajor !== null && currentMajor !== 21)) {
    const foundJava = javaCandidates.find((candidate) => existsSync(candidate))
    if (foundJava) env.JAVA_HOME = foundJava
  }
  const sdkCandidates = [
    path.join(env.HOME ?? "", "Library", "Android", "sdk"),
    "/opt/homebrew/share/android-commandlinetools",
    "/usr/local/share/android-commandlinetools",
  ]
  if (!env.ANDROID_HOME) {
    const foundSdk = sdkCandidates.find((candidate) => existsSync(candidate))
    if (foundSdk) env.ANDROID_HOME = foundSdk
  }
  env.ANDROID_SDK_ROOT ||= env.ANDROID_HOME
}

const isWindows = process.platform === "win32"
if (env.JAVA_HOME) {
  env.PATH = `${path.join(env.JAVA_HOME, "bin")}${path.delimiter}${env.PATH ?? ""}`
}
const gradleArgs = env.JAVA_HOME
  ? [
      `-Dorg.gradle.java.home=${env.JAVA_HOME}`,
      "-Dorg.gradle.java.installations.auto-detect=false",
      `-Dorg.gradle.java.installations.paths=${env.JAVA_HOME}`,
      ...tasks,
    ]
  : tasks
const executable = isWindows ? (process.env.ComSpec || "cmd.exe") : "./gradlew"
const args = isWindows ? ["/d", "/s", "/c", "gradlew.bat", ...gradleArgs] : gradleArgs
const result = spawnSync(executable, args, {
  cwd: androidRoot,
  env,
  shell: false,
  stdio: "inherit",
})

if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status ?? 1)
