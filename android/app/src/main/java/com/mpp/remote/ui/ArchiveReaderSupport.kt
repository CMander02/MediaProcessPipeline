package com.mpp.remote.ui

import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveDocument
import com.mpp.remote.network.parseSrt

internal data class ArchiveExportDocument(
    val fileName: String,
    val mimeType: String,
    val content: String,
)

internal fun archiveExportDocument(
    content: ArchiveContent,
    document: ArchiveDocument,
): ArchiveExportDocument {
    val body = content.contentFor(document)
    val isSubtitle = when (document) {
        ArchiveDocument.TRANSCRIPT -> content.transcriptSrt.isNotBlank()
        ArchiveDocument.EXTRA_POLISH -> parseSrt(body).isNotEmpty()
        else -> false
    }
    val extension = if (isSubtitle) "srt" else "md"
    val mimeType = if (isSubtitle) "application/x-subrip" else "text/markdown"
    val suffix = when (document) {
        ArchiveDocument.SUMMARY -> "摘要"
        ArchiveDocument.SOURCE -> "原文"
        ArchiveDocument.TRANSCRIPT -> "字幕"
        ArchiveDocument.EXTRA_POLISH -> "额外润色"
        ArchiveDocument.MINDMAP -> "思维导图"
        ArchiveDocument.DETAIL -> "详情"
    }
    return ArchiveExportDocument(
        fileName = "${sanitizeExportFileName(content.archive.title)}-$suffix.$extension",
        mimeType = mimeType,
        content = body,
    )
}

internal fun sanitizeExportFileName(value: String): String {
    val cleaned = value
        .replace(Regex("""[\\/:*?"<>|\u0000-\u001F]"""), "_")
        .trim()
        .trimEnd('.')
        .take(80)
    return cleaned.ifBlank { "MPP-内容" }
}
