package com.mpp.remote.network

import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveImage
import com.mpp.remote.data.ArchiveItem
import com.mpp.remote.data.ProcessingTarget
import com.mpp.remote.data.RemoteTask
import com.mpp.remote.data.ServerConfig
import com.mpp.remote.data.SubtitleCue
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class MppApiClient(private val config: ServerConfig) {
    fun testConnection() {
        request(method = "GET", path = "/api/tasks?limit=1")
    }

    fun createTask(
        source: String,
        requestedExecutor: ProcessingTarget,
    ): RemoteTask {
        val body = JSONObject()
            .put("task_type", "pipeline")
            .put("source", source)
            .put("options", JSONObject())
            .put("origin_client", "android")
            .put("requested_executor", requestedExecutor.storedValue)
            .toString()
        return parseTask(request(method = "POST", path = "/api/tasks", body = body))
    }

    fun listTasks(limit: Int = 50): List<RemoteTask> {
        val array = JSONArray(request(method = "GET", path = "/api/tasks?limit=$limit"))
        return buildList {
            for (index in 0 until array.length()) {
                add(parseTask(array.getJSONObject(index)))
            }
        }
    }

    fun getTask(taskId: String): RemoteTask =
        parseTask(request(method = "GET", path = "/api/tasks/$taskId"))

    fun listArchives(): List<ArchiveItem> {
        val payload = JSONObject(request(method = "GET", path = "/api/pipeline/archives?lite=true"))
        val archives = payload.optJSONArray("archives") ?: JSONArray()
        return buildList {
            for (index in 0 until archives.length()) {
                add(parseArchive(archives.getJSONObject(index)))
            }
        }
    }

    fun getArchiveContent(item: ArchiveItem): ArchiveContent {
        val encodedPath = encodeQuery(item.path)
        val detailPayload = JSONObject(
            request(
                method = "GET",
                path = "/api/pipeline/archives/detail?path=$encodedPath",
            ),
        )
        val archive = parseArchive(detailPayload.getJSONObject("archive"))
        val separator = if (archive.path.contains('\\')) "\\" else "/"
        fun read(filename: String): String = readFile("${archive.path}$separator$filename")

        val polishedSrt = read("transcript_polished.srt")
        val rawSrt = read("transcript.srt")
        val transcriptSrt = polishedSrt.ifBlank { rawSrt }
        val polishedMarkdown = read("transcript_polished.md")
        val extraPolish = read("transcript_extra_polished.md")
        val transcript = transcriptSrt
            .takeIf(String::isNotBlank)
            ?.let(::srtToReadableText)
            .orEmpty()
            .ifBlank { polishedMarkdown }

        return ArchiveContent(
            archive = archive,
            summary = read("summary.md"),
            transcript = transcript,
            transcriptSrt = transcriptSrt,
            subtitleCues = parseSrt(transcriptSrt),
            source = read("source.md").ifBlank { archive.description },
            mindmap = read("mindmap.md"),
            detail = read("detail.md"),
            extraPolish = extraPolish,
            images = parseArchiveImages(detailPayload.getJSONObject("archive")),
        )
    }

    fun loadThumbnail(archivePath: String): ByteArray =
        requestBytes(
            method = "GET",
            path = "/api/pipeline/archives/thumbnail?path=${encodeQuery(archivePath)}",
        )

    fun loadArchiveImage(path: String, maxEdge: Int): ByteArray =
        requestBytes(
            method = "GET",
            path = archiveImageRequestPath(path, maxEdge),
            readTimeoutMs = MEDIA_READ_TIMEOUT_MS,
        )

    fun polishArchive(archivePath: String, text: String): String {
        val body = JSONObject()
            .put("path", archivePath)
            .put("text", text)
            .put("source_filename", "transcript_polished.srt")
            .toString()
        val payload = JSONObject(
            request(
                method = "POST",
                path = "/api/pipeline/archives/polish",
                body = body,
                readTimeoutMs = POLISH_READ_TIMEOUT_MS,
            ),
        )
        return payload.optString("polished")
    }

    private fun readFile(path: String): String {
        val payload = JSONObject(
            request(
                method = "GET",
                path = "/api/filesystem/read?path=${encodeQuery(path)}",
            ),
        )
        return if (payload.optBoolean("success")) payload.optString("content") else ""
    }

    private fun request(
        method: String,
        path: String,
        body: String? = null,
        readTimeoutMs: Int = READ_TIMEOUT_MS,
    ): String =
        requestBytes(method, path, body, readTimeoutMs).toString(Charsets.UTF_8)

    private fun requestBytes(
        method: String,
        path: String,
        body: String? = null,
        readTimeoutMs: Int = READ_TIMEOUT_MS,
    ): ByteArray {
        val connection = URL(buildEndpoint(config.baseUrl, path))
            .openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = readTimeoutMs
            connection.setRequestProperty("Accept", "*/*")
            if (config.apiToken.isNotBlank()) {
                connection.setRequestProperty("Authorization", "Bearer ${config.apiToken}")
            }

            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setRequestProperty("X-Requested-With", "mpp-android")
                connection.outputStream.use { output ->
                    output.write(body.toByteArray(Charsets.UTF_8))
                }
            }

            val statusCode = connection.responseCode
            val response = readResponse(
                if (statusCode in 200..299) connection.inputStream else connection.errorStream,
            )
            if (statusCode !in 200..299) {
                val raw = response.toString(Charsets.UTF_8)
                throw MppApiException(statusCode, parseError(raw))
            }
            return response
        } finally {
            connection.disconnect()
        }
    }

    private fun parseTask(raw: String): RemoteTask = parseTask(JSONObject(raw))

    private fun parseTask(json: JSONObject): RemoteTask =
        RemoteTask(
            id = json.getString("id"),
            status = json.optString("status", "unknown"),
            source = json.optString("source"),
            progress = json.optDouble("progress", 0.0),
            message = json.optString("message"),
            createdAt = json.optString("created_at"),
            originClient = json.optString("origin_client"),
            requestedExecutor = json.optString("requested_executor"),
            assignedExecutor = json.optString("assigned_executor"),
        )

    private fun parseArchive(json: JSONObject): ArchiveItem {
        val metadata = json.optJSONObject("metadata") ?: JSONObject()
        val analysis = json.optJSONObject("analysis") ?: JSONObject()
        return ArchiveItem(
            path = json.optString("path"),
            title = json.optString("title").ifBlank { metadata.optString("title") },
            createdAt = json.optString("created_at").ifBlank { json.optString("date") },
            platform = metadata.optString("platform"),
            uploader = metadata.optString("uploader"),
            sourceUrl = metadata.optString("source_url"),
            contentSubtype = metadata.optString("content_subtype"),
            durationSeconds = json.optNullableDouble("duration_seconds")
                ?: metadata.optNullableDouble("duration_seconds"),
            hasSummary = json.optBoolean("has_summary"),
            hasTranscript = json.optBoolean("has_transcript"),
            hasMindmap = json.optBoolean("has_mindmap"),
            hasVideo = json.optBoolean("has_video"),
            hasAudio = json.optBoolean("has_audio"),
            hasImage = json.optBoolean("has_image"),
            processing = json.optBoolean("processing"),
            taskId = json.optString("task_id"),
            description = metadata.optString("description"),
            topics = analysis.optStringList("main_topics"),
            keywords = analysis.optStringList("keywords"),
        )
    }

    private fun parseArchiveImages(json: JSONObject): List<ArchiveImage> {
        val metadata = json.optJSONObject("metadata") ?: JSONObject()
        val extra = metadata.optJSONObject("extra") ?: JSONObject()
        val paths = buildList {
            extra.optJSONArray("downloaded_image_paths")?.let { values ->
                for (index in 0 until values.length()) {
                    values.optString(index).takeIf(String::isNotBlank)?.let(::add)
                }
            }
            extra.optJSONArray("images")?.let { values ->
                for (index in 0 until values.length()) {
                    val value = values.opt(index)
                    when (value) {
                        is String -> value.takeIf(String::isNotBlank)?.let(::add)
                        is JSONObject -> listOf("image_path", "path", "local_path")
                            .firstNotNullOfOrNull { key ->
                                value.optString(key).takeIf(String::isNotBlank)
                            }
                            ?.let(::add)
                    }
                }
            }
        }
        return paths
            .distinct()
            .mapIndexed { index, path ->
                ArchiveImage(path = path, label = "图片 ${index + 1}")
            }
    }

    private fun parseError(raw: String): String = runCatching {
        val json = JSONObject(raw)
        json.optString("detail").ifBlank {
            json.optString("error").ifBlank { raw }
        }
    }.getOrDefault(raw.ifBlank { "服务器返回未知错误" })

    private fun readResponse(stream: InputStream?): ByteArray {
        if (stream == null) return ByteArray(0)
        return stream.use { input ->
            val output = ByteArrayOutputStream()
            input.copyTo(output)
            output.toByteArray()
        }
    }

    private fun encodeQuery(value: String): String =
        URLEncoder.encode(value, Charsets.UTF_8.name()).replace("+", "%20")

    private companion object {
        const val CONNECT_TIMEOUT_MS = 10_000
        const val READ_TIMEOUT_MS = 30_000
        const val MEDIA_READ_TIMEOUT_MS = 90_000
        const val POLISH_READ_TIMEOUT_MS = 300_000
    }
}

internal fun archiveImageRequestPath(path: String, maxEdge: Int): String {
    require(maxEdge in 256..4096) { "maxEdge must be between 256 and 4096" }
    val encodedPath = URLEncoder
        .encode(path, Charsets.UTF_8.name())
        .replace("+", "%20")
    return "/api/pipeline/archives/image?path=$encodedPath&max_edge=$maxEdge"
}

internal fun parseSrt(raw: String): List<SubtitleCue> {
    if (raw.isBlank()) return emptyList()
    val timestamp = Regex(
        """^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3}).*$""",
    )
    val speaker = Regex("""^\[([^\]]+)]\s*""")
    return raw
        .replace("\r\n", "\n")
        .replace('\r', '\n')
        .trim()
        .split(Regex("""\n\s*\n+"""))
        .mapNotNull { block ->
            val lines = block.lines().map(String::trimEnd)
            if (lines.size < 3) return@mapNotNull null
            val timing = timestamp.matchEntire(lines[1].trim()) ?: return@mapNotNull null
            var text = lines.drop(2).joinToString("\n").trim()
            val speakerMatch = speaker.find(text)
            val speakerName = speakerMatch?.groupValues?.get(1).orEmpty()
            if (speakerMatch != null) text = text.removeRange(speakerMatch.range).trim()
            SubtitleCue(
                index = lines[0].trim().removePrefix("\uFEFF").toIntOrNull() ?: 0,
                startTimeMs = timestampToMillis(timing.groupValues, offset = 1),
                endTimeMs = timestampToMillis(timing.groupValues, offset = 5),
                text = text,
                speaker = speakerName,
            )
        }
}

private fun timestampToMillis(values: List<String>, offset: Int): Long =
    values[offset].toLong() * 3_600_000L +
        values[offset + 1].toLong() * 60_000L +
        values[offset + 2].toLong() * 1_000L +
        values[offset + 3].toLong()

internal fun formatSubtitleTime(timeMs: Long): String {
    val totalSeconds = timeMs.coerceAtLeast(0L) / 1_000L
    val hours = totalSeconds / 3_600L
    val minutes = (totalSeconds % 3_600L) / 60L
    val seconds = totalSeconds % 60L
    return if (hours > 0L) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%02d:%02d".format(minutes, seconds)
    }
}

internal fun srtToReadableText(raw: String): String {
    val cues = parseSrt(raw)
    if (cues.isNotEmpty()) return cues.joinToString("\n") { cue ->
        listOf(cue.speaker.takeIf(String::isNotBlank), cue.text)
            .filterNotNull()
            .joinToString("：")
    }
    val timestamp = Regex("""^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}.*$""")
    return raw.lineSequence()
        .map(String::trim)
        .filterNot { line -> line.isBlank() || line.all(Char::isDigit) || timestamp.matches(line) }
        .joinToString("\n")
}

private fun JSONObject.optNullableDouble(key: String): Double? =
    if (has(key) && !isNull(key)) optDouble(key) else null

private fun JSONObject.optStringList(key: String): List<String> {
    val values = optJSONArray(key) ?: return emptyList()
    return buildList {
        for (index in 0 until values.length()) {
            values.optString(index).takeIf(String::isNotBlank)?.let(::add)
        }
    }
}

class MppApiException(
    val statusCode: Int,
    message: String,
) : RuntimeException("HTTP $statusCode：$message")
