import { createHash } from "node:crypto"
import {
  copyFile,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises"
import { constants as fsConstants } from "node:fs"
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const scriptDir = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(scriptDir, "..", "..")
const runtimeRoot = resolve(projectRoot, "web", "src-tauri", "resources", "runtime")
const runtimeParent = dirname(runtimeRoot)
const toolContractSource = join(projectRoot, "packaging", "desktop-tools.json")
const toolContractRuntimePath = "packaging/desktop-tools.json"

if (
  runtimeRoot !== resolve(projectRoot, "web", "src-tauri", "resources", "runtime") ||
  !isPathInside(projectRoot, runtimeRoot)
) {
  throw new Error(`Refusing to stage an unexpected runtime directory: ${runtimeRoot}`)
}

const forbiddenParts = new Set([
  ".env",
  ".envrc",
  ".git-credentials",
  ".netrc",
  ".npmrc",
  ".pypirc",
  ".ssh",
  ".aws",
  "__pycache__",
  "auth.json",
  "config.json",
  "cookies.json",
  "cookies.txt",
  "credentials.json",
  "client_secret.json",
  "id_dsa",
  "id_ed25519",
  "id_ecdsa",
  "id_rsa",
  "secrets.json",
  "service-account.json",
  "settings.json",
  "storage_state.json",
  "token.json",
  "tokens.json",
])
const forbiddenSuffixes = [
  ".crt",
  ".der",
  ".egg-info",
  ".jks",
  ".key",
  ".keystore",
  ".p12",
  ".pem",
  ".pfx",
  ".pkcs12",
  ".ppk",
  ".pyc",
  ".pyo",
  ".secret",
  ".secrets",
]
const sha256Pattern = /^[0-9a-f]{64}$/

function isPathInside(root, candidate) {
  const pathFromRoot = relative(resolve(root), resolve(candidate))
  return (
    pathFromRoot.length > 0 &&
    pathFromRoot !== ".." &&
    !pathFromRoot.startsWith(`..${sep}`) &&
    !isAbsolute(pathFromRoot)
  )
}

function toPosix(pathValue) {
  return pathValue.split(sep).join("/")
}

function assertSafeRelativePath(pathValue) {
  const normalized = toPosix(pathValue)
  const parts = normalized.toLowerCase().split("/")
  const forbidden = parts.find(
    (part) =>
      forbiddenParts.has(part) ||
      part.startsWith(".env.") ||
      forbiddenSuffixes.some((suffix) => part.endsWith(suffix)),
  )
  if (
    forbidden ||
    !normalized ||
    normalized.startsWith("/") ||
    normalized.includes("\\") ||
    parts.includes("..") ||
    normalized.includes("\0")
  ) {
    throw new Error(`Forbidden desktop runtime path: ${normalized}`)
  }
  return normalized
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    encoding: "utf8",
    windowsHide: true,
    ...options,
  })
  if (result.error) {
    throw result.error
  }
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "").trim()
    throw new Error(`${command} ${args.join(" ")} failed (${result.status}): ${detail}`)
  }
  return (result.stdout || "").trim()
}

async function assertRegularSource(source, allowedRealRoot = null) {
  const sourceInfo = await lstat(source)
  if (!sourceInfo.isFile() || sourceInfo.isSymbolicLink()) {
    throw new Error(`Runtime sources must be regular non-link files: ${source}`)
  }
  if (allowedRealRoot) {
    const canonicalSource = await realpath(source)
    if (!isPathInside(allowedRealRoot, canonicalSource)) {
      throw new Error(`Runtime source escapes its allowed root: ${source}`)
    }
  }
}

async function copyRegularFile(source, destination, allowedRealRoot = null) {
  await assertRegularSource(source, allowedRealRoot)
  await mkdir(dirname(destination), { recursive: true })
  await copyFile(source, destination, fsConstants.COPYFILE_FICLONE)
}

async function copyDirectory(sourceRoot, destinationRoot, stageRoot, allowedRealRoot) {
  const sourceInfo = await lstat(sourceRoot)
  if (!sourceInfo.isDirectory() || sourceInfo.isSymbolicLink()) {
    throw new Error(`Runtime source directories must be real directories: ${sourceRoot}`)
  }
  const canonicalSource = await realpath(sourceRoot)
  if (
    canonicalSource !== allowedRealRoot &&
    !isPathInside(allowedRealRoot, canonicalSource)
  ) {
    throw new Error(`Runtime source directory escapes its allowed root: ${sourceRoot}`)
  }

  const entries = await readdir(sourceRoot, { withFileTypes: true })
  entries.sort((left, right) => left.name.localeCompare(right.name))
  for (const entry of entries) {
    const source = join(sourceRoot, entry.name)
    const target = join(destinationRoot, entry.name)
    assertSafeRelativePath(relative(stageRoot, target))
    if (entry.isSymbolicLink()) {
      throw new Error(`Runtime source symlinks or junctions are not allowed: ${source}`)
    }
    if (entry.isDirectory()) {
      await copyDirectory(source, target, stageRoot, allowedRealRoot)
    } else if (entry.isFile()) {
      await copyRegularFile(source, target, allowedRealRoot)
    } else {
      throw new Error(`Unsupported runtime source entry: ${source}`)
    }
  }
}

function trackedBackendFiles() {
  const result = spawnSync("git", ["ls-files", "-z", "--", "backend/app"], {
    cwd: projectRoot,
    encoding: "buffer",
    windowsHide: true,
  })
  if (result.error) {
    throw result.error
  }
  if (result.status !== 0) {
    throw new Error(`git ls-files failed (${result.status})`)
  }
  return result.stdout
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
    .sort()
}

function locateUv() {
  const override = process.env.MPP_BUNDLED_UV?.trim()
  if (override) {
    return resolve(override)
  }
  const output = run("where.exe", ["uv"])
  const first = output.split(/\r?\n/).find(Boolean)
  if (!first) {
    throw new Error("uv executable was not found")
  }
  return resolve(first.trim())
}

function requiredUvVersion(pyprojectText) {
  const table = pyprojectText.match(/\[tool\.uv\]([\s\S]*?)(?=\n\[|$)/)
  const match = table?.[1].match(/required-version\s*=\s*["']==([^"']+)["']/)
  if (!match) {
    throw new Error("pyproject.toml must pin [tool.uv].required-version with ==")
  }
  return match[1]
}

function requireString(value, label) {
  if (typeof value !== "string" || !value) {
    throw new Error(`${label} must be a non-empty string`)
  }
  return value
}

async function loadToolContract() {
  const sourceText = await readFile(toolContractSource, "utf8")
  let contract
  try {
    contract = JSON.parse(sourceText)
  } catch (error) {
    throw new Error(`Invalid desktop tool contract: ${error}`)
  }
  const uv = contract?.tools?.uv
  if (contract?.schema !== 1 || !uv) {
    throw new Error("Desktop tool contract must define schema 1 and tools.uv")
  }
  if (
    uv.platform?.os !== "windows" ||
    uv.platform?.arch !== "x64" ||
    uv.platform?.target !== "x86_64-pc-windows-msvc"
  ) {
    throw new Error("Desktop uv contract must target Windows x64 MSVC")
  }
  requireString(uv.version, "tools.uv.version")
  requireString(uv.source?.url, "tools.uv.source.url")
  if (!sha256Pattern.test(uv.source?.archiveSha256 || "")) {
    throw new Error("tools.uv.source.archiveSha256 must be a lowercase SHA-256")
  }
  if (uv.binary?.runtimePath !== "bin/uv.exe") {
    throw new Error("tools.uv.binary.runtimePath must be bin/uv.exe")
  }
  if (!sha256Pattern.test(uv.binary?.sha256 || "")) {
    throw new Error("tools.uv.binary.sha256 must be a lowercase SHA-256")
  }
  if (!Number.isSafeInteger(uv.binary?.size) || uv.binary.size <= 0) {
    throw new Error("tools.uv.binary.size must be a positive integer")
  }
  if (uv.binary?.peMachine !== "0x8664") {
    throw new Error("tools.uv.binary.peMachine must be 0x8664")
  }
  if (
    uv.license?.spdx !== "MIT" ||
    uv.license?.sourcePath !== "packaging/third-party-licenses/uv-LICENSE-MIT.txt" ||
    uv.license?.runtimePath !== "third-party-licenses/uv-LICENSE-MIT.txt" ||
    !sha256Pattern.test(uv.license?.sha256 || "")
  ) {
    throw new Error("Desktop uv contract must declare the pinned MIT license")
  }
  return { contract, sourceText, uv }
}

async function walkFiles(root, current = root) {
  const files = []
  const entries = await readdir(current, { withFileTypes: true })
  entries.sort((left, right) => left.name.localeCompare(right.name))
  for (const entry of entries) {
    const absolute = join(current, entry.name)
    if (entry.isSymbolicLink()) {
      throw new Error(`Runtime staging contains a symlink or junction: ${absolute}`)
    }
    if (entry.isDirectory()) {
      files.push(...(await walkFiles(root, absolute)))
    } else if (entry.isFile()) {
      files.push(absolute)
    } else {
      throw new Error(`Runtime staging contains an unsupported entry: ${absolute}`)
    }
  }
  return files
}

async function sha256(pathValue) {
  return createHash("sha256").update(await readFile(pathValue)).digest("hex")
}

async function peMachine(pathValue) {
  const handle = await open(pathValue, "r")
  try {
    const dosHeader = Buffer.alloc(64)
    const dosRead = await handle.read(dosHeader, 0, dosHeader.length, 0)
    if (dosRead.bytesRead !== dosHeader.length || dosHeader.toString("ascii", 0, 2) !== "MZ") {
      throw new Error("missing DOS MZ header")
    }
    const peOffset = dosHeader.readUInt32LE(0x3c)
    if (peOffset < 64 || peOffset > 16 * 1024 * 1024) {
      throw new Error(`invalid PE header offset ${peOffset}`)
    }
    const peHeader = Buffer.alloc(6)
    const peRead = await handle.read(peHeader, 0, peHeader.length, peOffset)
    if (
      peRead.bytesRead !== peHeader.length ||
      !peHeader.subarray(0, 4).equals(Buffer.from([0x50, 0x45, 0, 0]))
    ) {
      throw new Error("missing PE signature")
    }
    return `0x${peHeader.readUInt16LE(4).toString(16).padStart(4, "0")}`
  } finally {
    await handle.close()
  }
}

async function validateUv(uvSource, expected) {
  await assertRegularSource(uvSource)
  const uvOutput = run(uvSource, ["--version"])
  const actualVersion = uvOutput.match(/\buv\s+([0-9][^\s]*)/)?.[1]
  if (actualVersion !== expected.version) {
    throw new Error(
      `Bundled uv version mismatch: expected ${expected.version}, received ${uvOutput}`,
    )
  }
  const fileInfo = await stat(uvSource)
  if (fileInfo.size !== expected.binary.size) {
    throw new Error(
      `Bundled uv size mismatch: expected ${expected.binary.size}, received ${fileInfo.size}`,
    )
  }
  const actualHash = await sha256(uvSource)
  if (actualHash !== expected.binary.sha256) {
    throw new Error(
      `Bundled uv SHA-256 mismatch: expected ${expected.binary.sha256}, received ${actualHash}`,
    )
  }
  const actualMachine = await peMachine(uvSource)
  if (actualMachine !== expected.binary.peMachine) {
    throw new Error(
      `Bundled uv PE machine mismatch: expected ${expected.binary.peMachine}, received ${actualMachine}`,
    )
  }
}

async function promoteStage(stageRoot) {
  const backupRoot = join(
    runtimeParent,
    `.runtime-previous-${process.pid}-${Date.now().toString(36)}`,
  )
  let movedExisting = false
  try {
    if (await lstat(runtimeRoot).catch(() => null)) {
      await rename(runtimeRoot, backupRoot)
      movedExisting = true
    }
    await rename(stageRoot, runtimeRoot)
  } catch (error) {
    if (movedExisting && !(await lstat(runtimeRoot).catch(() => null))) {
      await rename(backupRoot, runtimeRoot).catch(() => {})
    }
    throw error
  }
  if (movedExisting) {
    await rm(backupRoot, { recursive: true, force: true })
  }
}

async function main() {
  if (process.platform !== "win32" || process.arch !== "x64") {
    throw new Error(
      `Desktop runtime staging supports only Windows x64; received ${process.platform}/${process.arch}`,
    )
  }

  const dirtyStatus = run("git", [
    "status",
    "--porcelain=v1",
    "--untracked-files=normal",
  ])
  const sourceDirty = Boolean(dirtyStatus)
  if (sourceDirty && process.env.MPP_ALLOW_DIRTY_RUNTIME_STAGE !== "1") {
    throw new Error(
      "Refusing to stage a dirty Git worktree. Commit/stash changes, or set " +
        "MPP_ALLOW_DIRTY_RUNTIME_STAGE=1 for an explicitly marked development runtime.",
    )
  }

  const pyprojectText = await readFile(join(projectRoot, "pyproject.toml"), "utf8")
  const expectedUvVersion = requiredUvVersion(pyprojectText)
  const { sourceText: toolContractText, uv: uvContract } = await loadToolContract()
  if (uvContract.version !== expectedUvVersion) {
    throw new Error(
      `Desktop uv contract ${uvContract.version} differs from pyproject required-version ${expectedUvVersion}`,
    )
  }

  const uvSource = await realpath(locateUv())
  await validateUv(uvSource, uvContract)

  const webDist = join(projectRoot, "web", "dist")
  const webInfo = await lstat(webDist).catch(() => null)
  if (!webInfo?.isDirectory() || webInfo.isSymbolicLink()) {
    throw new Error("web/dist must be a real production build directory")
  }
  const canonicalProjectRoot = await realpath(projectRoot)
  const canonicalWebDist = await realpath(webDist)
  if (!isPathInside(canonicalProjectRoot, canonicalWebDist)) {
    throw new Error("web/dist real path escapes the project root")
  }
  const webIndex = join(webDist, "index.html")
  if (!(await stat(webIndex).catch(() => null))?.isFile()) {
    throw new Error("web/dist/index.html is missing; run the production Web build first")
  }

  await mkdir(runtimeParent, { recursive: true })
  const stageRoot = await mkdtemp(join(runtimeParent, ".runtime-stage-"))
  let promoted = false
  try {
    for (const filename of ["VERSION", "pyproject.toml", "uv.lock"]) {
      await copyRegularFile(join(projectRoot, filename), join(stageRoot, filename))
    }

    const trackedFiles = trackedBackendFiles()
    if (!trackedFiles.includes("backend/app/__init__.py")) {
      throw new Error("Tracked backend package is incomplete")
    }
    for (const trackedFile of trackedFiles) {
      const safeTarget = assertSafeRelativePath(trackedFile)
      await copyRegularFile(join(projectRoot, trackedFile), join(stageRoot, safeTarget))
    }

    await copyDirectory(
      webDist,
      join(stageRoot, "web", "dist"),
      stageRoot,
      canonicalWebDist,
    )
    await copyRegularFile(
      toolContractSource,
      join(stageRoot, toolContractRuntimePath),
    )
    const licenseSource = join(projectRoot, uvContract.license.sourcePath)
    await assertRegularSource(licenseSource)
    const licenseText = (await readFile(licenseSource, "utf8"))
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
    const licenseBytes = Buffer.from(licenseText, "utf8")
    if (
      createHash("sha256").update(licenseBytes).digest("hex") !==
      uvContract.license.sha256
    ) {
      throw new Error("Pinned uv MIT license SHA-256 differs from the tool contract")
    }
    const licenseDestination = join(stageRoot, uvContract.license.runtimePath)
    await mkdir(dirname(licenseDestination), { recursive: true })
    await writeFile(
      licenseDestination,
      licenseBytes,
    )
    await copyRegularFile(
      uvSource,
      join(stageRoot, uvContract.binary.runtimePath),
    )

    const files = []
    for (const absolute of await walkFiles(stageRoot)) {
      const relativePath = assertSafeRelativePath(relative(stageRoot, absolute))
      if (relativePath === "runtime-manifest.json") {
        continue
      }
      const fileInfo = await stat(absolute)
      files.push({
        path: relativePath,
        size: fileInfo.size,
        sha256: await sha256(absolute),
      })
    }
    files.sort((left, right) => left.path.localeCompare(right.path))

    const version = (await readFile(join(projectRoot, "VERSION"), "utf8")).trim()
    if (!version) {
      throw new Error("VERSION must be non-empty")
    }
    const sourceCommit = run("git", ["rev-parse", "HEAD"])
    if (!/^[0-9a-f]{40}$/.test(sourceCommit)) {
      throw new Error(`Git returned an invalid source commit: ${sourceCommit}`)
    }
    const uvRecord = files.find(
      (file) => file.path === uvContract.binary.runtimePath,
    )
    const contractRecord = files.find(
      (file) => file.path === toolContractRuntimePath,
    )
    if (
      uvRecord?.sha256 !== uvContract.binary.sha256 ||
      uvRecord?.size !== uvContract.binary.size
    ) {
      throw new Error("Staged uv binary differs from the desktop tool contract")
    }
    if (
      !contractRecord ||
      contractRecord.sha256 !==
        createHash("sha256").update(toolContractText).digest("hex")
    ) {
      throw new Error("Staged desktop tool contract differs from its source")
    }

    const manifest = {
      schema: 1,
      appVersion: version,
      sourceCommit,
      sourceDirty,
      toolContract: toolContractRuntimePath,
      uv: {
        version: uvContract.version,
        path: uvContract.binary.runtimePath,
        sha256: uvRecord.sha256,
        size: uvRecord.size,
        peMachine: uvContract.binary.peMachine,
      },
      files,
    }
    await writeFile(
      join(stageRoot, "runtime-manifest.json"),
      `${JSON.stringify(manifest, null, 2)}\n`,
      "utf8",
    )

    await promoteStage(stageRoot)
    promoted = true
    console.log(
      `[desktop-runtime] staged ${files.length} files for v${version} with uv ${expectedUvVersion}`,
    )
    console.log(
      `[desktop-runtime] source ${sourceCommit}${sourceDirty ? " (dirty development build)" : ""}`,
    )
    console.log(`[desktop-runtime] ${runtimeRoot}`)
  } finally {
    if (!promoted) {
      await rm(stageRoot, { recursive: true, force: true })
    }
  }
}

await main()
