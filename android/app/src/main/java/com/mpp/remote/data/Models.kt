package com.mpp.remote.data

data class ServerConfig(
    val baseUrl: String = "",
    val apiToken: String = "",
)

enum class ProcessingTarget(
    val storedValue: String,
    val label: String,
) {
    SERVER("server", "Server"),
    EXE("exe", "EXE");

    companion object {
        fun fromStoredValue(value: String?): ProcessingTarget =
            entries.firstOrNull { it.storedValue == value } ?: SERVER
    }
}

enum class ThemeMode(val storedValue: String) {
    SYSTEM("system"),
    LIGHT("light"),
    DARK("dark");

    companion object {
        fun fromStoredValue(value: String?): ThemeMode =
            entries.firstOrNull { it.storedValue == value } ?: SYSTEM
    }
}

data class RemoteTask(
    val id: String,
    val status: String,
    val source: String,
    val progress: Double = 0.0,
    val message: String = "",
    val createdAt: String = "",
    val originClient: String = "",
    val requestedExecutor: String = "",
    val assignedExecutor: String = "",
) {
    val progressPercent: Int
        get() = (progress.coerceIn(0.0, 1.0) * 100).toInt()
}

data class ArchiveItem(
    val path: String,
    val title: String,
    val createdAt: String = "",
    val platform: String = "",
    val uploader: String = "",
    val sourceUrl: String = "",
    val contentSubtype: String = "",
    val durationSeconds: Double? = null,
    val hasSummary: Boolean = false,
    val hasTranscript: Boolean = false,
    val hasMindmap: Boolean = false,
    val hasVideo: Boolean = false,
    val hasAudio: Boolean = false,
    val hasImage: Boolean = false,
    val processing: Boolean = false,
    val taskId: String = "",
    val description: String = "",
    val topics: List<String> = emptyList(),
    val keywords: List<String> = emptyList(),
)

data class SubtitleCue(
    val index: Int,
    val startTimeMs: Long,
    val endTimeMs: Long,
    val text: String,
    val speaker: String = "",
)

data class ArchiveImage(
    val path: String,
    val label: String = "",
)

data class ArchiveContent(
    val archive: ArchiveItem,
    val summary: String = "",
    val transcript: String = "",
    val transcriptSrt: String = "",
    val subtitleCues: List<SubtitleCue> = emptyList(),
    val source: String = "",
    val mindmap: String = "",
    val detail: String = "",
    val extraPolish: String = "",
    val images: List<ArchiveImage> = emptyList(),
) {
    val initialDocument: ArchiveDocument
        get() = when {
            summary.isNotBlank() -> ArchiveDocument.SUMMARY
            source.isNotBlank() -> ArchiveDocument.SOURCE
            transcriptSrt.isNotBlank() || transcript.isNotBlank() -> ArchiveDocument.TRANSCRIPT
            mindmap.isNotBlank() -> ArchiveDocument.MINDMAP
            else -> ArchiveDocument.DETAIL
        }

    fun contentFor(document: ArchiveDocument): String = when (document) {
        ArchiveDocument.SUMMARY -> summary
        ArchiveDocument.SOURCE -> source
        ArchiveDocument.TRANSCRIPT -> transcriptSrt.ifBlank { transcript }
        ArchiveDocument.EXTRA_POLISH -> extraPolish
        ArchiveDocument.MINDMAP -> mindmap
        ArchiveDocument.DETAIL -> detail
    }
}

enum class ArchiveDocument(val label: String) {
    SUMMARY("摘要"),
    SOURCE("原文"),
    TRANSCRIPT("字幕"),
    EXTRA_POLISH("额外润色"),
    MINDMAP("导图"),
    DETAIL("详情"),
}
