package com.mpp.remote.network

import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveItem
import com.mpp.remote.data.RemoteTask
import com.mpp.remote.data.ServerConfig
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

    fun createTask(source: String): RemoteTask {
        val body = JSONObject()
            .put("task_type", "pipeline")
            .put("source", source)
            .put("options", JSONObject())
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

        val polishedMarkdown = read("transcript_polished.md")
        val polishedSrt = if (polishedMarkdown.isBlank()) read("transcript_polished.srt") else ""
        val rawSrt = if (polishedMarkdown.isBlank() && polishedSrt.isBlank()) read("transcript.srt") else ""
        val transcript = when {
            polishedMarkdown.isNotBlank() -> polishedMarkdown
            polishedSrt.isNotBlank() -> srtToReadableText(polishedSrt)
            rawSrt.isNotBlank() -> srtToReadableText(rawSrt)
            else -> ""
        }

        return ArchiveContent(
            archive = archive,
            summary = read("summary.md"),
            transcript = transcript,
            source = read("source.md").ifBlank { archive.description },
            mindmap = read("mindmap.md"),
            detail = read("detail.md"),
        )
    }

    fun loadThumbnail(archivePath: String): ByteArray =
        requestBytes(
            method = "GET",
            path = "/api/pipeline/archives/thumbnail?path=${encodeQuery(archivePath)}",
        )

    private fun readFile(path: String): String {
        val payload = JSONObject(
            request(
                method = "GET",
                path = "/api/filesystem/read?path=${encodeQuery(path)}",
            ),
        )
        return if (payload.optBoolean("success")) payload.optString("content") else ""
    }

    private fun request(method: String, path: String, body: String? = null): String =
        requestBytes(method, path, body).toString(Charsets.UTF_8)

    private fun requestBytes(method: String, path: String, body: String? = null): ByteArray {
        val connection = URL(buildEndpoint(config.baseUrl, path))
            .openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
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
            durationSeconds = metadata.optNullableDouble("duration_seconds"),
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
    }
}

internal fun srtToReadableText(raw: String): String {
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
