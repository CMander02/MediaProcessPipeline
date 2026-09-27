import { api } from "@/lib/api"
import { parseSRT, subtitlesToPlainText } from "@/lib/srt"
import { safeFileName, type ZipEntry } from "@/lib/zip"

/** Generated text files copied into an export, in the order they are listed. */
const TEXT_FILES = ["summary.md", "mindmap.md", "detail.md", "source.md"] as const

function joinPath(base: string, name: string): string {
  const separator = base.includes("\\") ? "\\" : "/"
  return base.replace(/[\\/]+$/, "") + separator + name
}

async function readOptional(path: string): Promise<string> {
  try {
    return (await api.filesystem.read(path)).content ?? ""
  } catch {
    return ""
  }
}

/** The text outputs of one archive, as zip entries inside a folder named after it. */
export async function archiveExportEntries(
  archive: { path: string; title: string },
  folder = safeFileName(archive.title),
): Promise<ZipEntry[]> {
  const [texts, polished, raw] = await Promise.all([
    Promise.all(TEXT_FILES.map((name) => readOptional(joinPath(archive.path, name)))),
    readOptional(joinPath(archive.path, "transcript_polished.srt")),
    readOptional(joinPath(archive.path, "transcript.srt")),
  ])
  const entries: ZipEntry[] = []
  TEXT_FILES.forEach((name, index) => {
    if (texts[index]) entries.push({ name: `${folder}/${name}`, content: texts[index] })
  })
  const srt = polished || raw
  if (srt) {
    entries.push({ name: `${folder}/transcript.srt`, content: srt })
    entries.push({ name: `${folder}/transcript.txt`, content: subtitlesToPlainText(parseSRT(srt)) })
  }
  return entries
}

/** Folder names for several archives, made unique when titles repeat. */
export function uniqueFolders(titles: string[]): string[] {
  const seen = new Map<string, number>()
  return titles.map((title) => {
    const base = safeFileName(title)
    const count = (seen.get(base) ?? 0) + 1
    seen.set(base, count)
    return count === 1 ? base : `${base} (${count})`
  })
}
