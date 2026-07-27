package com.mpp.remote.network

import java.net.URI

fun normalizeServerUrl(raw: String): String {
    val trimmed = raw.trim().trimEnd('/')
    require(trimmed.isNotEmpty()) { "请输入服务器地址" }

    val uri = runCatching { URI(trimmed) }
        .getOrElse { throw IllegalArgumentException("服务器地址格式无效") }
    require(uri.scheme == "http" || uri.scheme == "https") {
        "服务器地址需要以 http:// 或 https:// 开头"
    }
    require(!uri.host.isNullOrBlank()) { "服务器地址缺少主机名" }
    require(uri.userInfo == null && uri.query == null && uri.fragment == null) {
        "服务器地址不能包含账号、查询参数或片段"
    }
    return trimmed
}

fun buildEndpoint(baseUrl: String, path: String): String {
    val normalizedBase = normalizeServerUrl(baseUrl)
    return "$normalizedBase/${path.trimStart('/')}"
}
