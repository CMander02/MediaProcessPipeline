import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
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

const env = { ...process.env }
if (process.platform === "win32") {
  env.JAVA_HOME ||= "C:\\Program Files\\Android\\Android Studio\\jbr"
  env.ANDROID_HOME ||= path.join(env.LOCALAPPDATA ?? "", "Android", "Sdk")
  env.ANDROID_SDK_ROOT ||= env.ANDROID_HOME
}

const isWindows = process.platform === "win32"
const executable = isWindows ? (process.env.ComSpec || "cmd.exe") : "./gradlew"
const args = isWindows ? ["/d", "/s", "/c", "gradlew.bat", ...tasks] : tasks
const result = spawnSync(executable, args, {
  cwd: androidRoot,
  env,
  shell: false,
  stdio: "inherit",
})

if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status ?? 1)
