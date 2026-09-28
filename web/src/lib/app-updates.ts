/**
 * Keeping the page on the current build without anyone having to click 刷新.
 *
 * A new build is loaded at a moment when nothing is lost by it: while the window is in the
 * background (unless something is playing) or when the user moves to another page anyway.
 * The desktop app uses no service worker at all: its page always comes from the local daemon,
 * so a cached copy could only ever be an old one.
 */

const UPDATED_KEY = "mpp-updated"
const CHUNK_RELOAD_KEY = "mpp-chunk-reload"
const CHECK_EVERY_MS = 5 * 60_000
// Focus changes come in bursts; one check per half minute is plenty.
const MIN_CHECK_GAP_MS = 30_000

let reloadPending = false
let reloading = false

function mediaPlaying(): boolean {
  return [...document.querySelectorAll<HTMLMediaElement>("video, audio")]
    .some((media) => !media.paused && !media.ended)
}

function remember(key: string) {
  try {
    sessionStorage.setItem(key, String(Date.now()))
  } catch {
    // Only used for a toast and a loop guard.
  }
}

function reloadIntoNewBuild() {
  if (reloading) return
  reloading = true
  remember(UPDATED_KEY)
  window.location.reload()
}

/** Load the new build at the next moment nobody would notice. */
export function scheduleUpdateReload(): void {
  if (reloadPending) return
  reloadPending = true
  if (document.visibilityState === "hidden" && !mediaPlaying()) {
    reloadIntoNewBuild()
    return
  }
  const listening = new AbortController()
  const reloadNow = () => {
    listening.abort()
    reloadIntoNewBuild()
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && !mediaPlaying()) reloadNow()
  }, { signal: listening.signal })
  // Going to another page replaces what is on screen anyway; take the new build along.
  window.addEventListener("hashchange", reloadNow, { signal: listening.signal })
}

/** True once, right after a reload that brought in a new build. */
export function consumeUpdatedFlag(): boolean {
  try {
    const updated = sessionStorage.getItem(UPDATED_KEY) !== null
    sessionStorage.removeItem(UPDATED_KEY)
    return updated
  } catch {
    return false
  }
}

/**
 * A lazily loaded part of the old build is gone from the server, which only happens after an
 * update: reload once into the new build (not again if that did not help).
 */
export function reloadAfterMissingChunk(): boolean {
  try {
    const last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) ?? 0)
    if (Date.now() - last < 60_000) return false
  } catch {
    return false
  }
  remember(CHUNK_RELOAD_KEY)
  reloadIntoNewBuild()
  return true
}

/** The build's entry script in an index.html, e.g. "/assets/index-CGE2j0fF.js". */
export function entryScript(html: string): string | null {
  return html.match(/src="([^"]*\/assets\/index-[^"]+\.js)"/)?.[1] ?? null
}

/** Look for a new build now and then, and whenever the window comes back to the front. */
export function watchForNewBuild(check: () => Promise<boolean>, everyMs = CHECK_EVERY_MS): () => void {
  let lastCheck = 0
  const run = async () => {
    if (reloadPending || Date.now() - lastCheck < MIN_CHECK_GAP_MS) return
    lastCheck = Date.now()
    try {
      if (await check()) scheduleUpdateReload()
    } catch {
      // Offline or the daemon is restarting: the next check will tell.
    }
  }
  const onVisible = () => {
    if (document.visibilityState === "visible") void run()
  }
  const timer = window.setInterval(() => void run(), everyMs)
  document.addEventListener("visibilitychange", onVisible)
  window.addEventListener("focus", onVisible)
  return () => {
    window.clearInterval(timer)
    document.removeEventListener("visibilitychange", onVisible)
    window.removeEventListener("focus", onVisible)
  }
}

/** Whether the daemon serves a different build than the one running here. */
export async function serverHasNewBuild(): Promise<boolean> {
  const running = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')
    ?.getAttribute("src")
  if (!running) return false
  const response = await fetch(`/index.html?build-check=${Date.now()}`, { cache: "no-store" })
  const latest = response.ok ? entryScript(await response.text()) : null
  return latest !== null && latest !== running
}

/**
 * The desktop app does without a service worker. Remove one left by an earlier version, with
 * its cache, and reload if the page itself came from that cache.
 */
export async function removeServiceWorkers(): Promise<void> {
  if (!("serviceWorker" in navigator)) return
  const registrations = await navigator.serviceWorker.getRegistrations()
  await Promise.all(registrations.map((registration) => registration.unregister()))
  if ("caches" in window) {
    const keys = await caches.keys()
    await Promise.all(keys.filter((key) => key.startsWith("workbox-")).map((key) => caches.delete(key)))
  }
  if (registrations.length > 0 && navigator.serviceWorker.controller) reloadIntoNewBuild()
}
