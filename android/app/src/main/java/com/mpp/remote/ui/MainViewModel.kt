package com.mpp.remote.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveItem
import com.mpp.remote.data.ProcessingTarget
import com.mpp.remote.data.RecentTaskStore
import com.mpp.remote.data.RemoteTask
import com.mpp.remote.data.SecureConfigStore
import com.mpp.remote.data.ServerConfig
import com.mpp.remote.data.ThemeMode
import com.mpp.remote.network.MppApiClient
import com.mpp.remote.network.normalizeServerUrl
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class MainUiState(
    val baseUrl: String = "",
    val apiToken: String = "",
    val sharedText: String = "",
    val recentTasks: List<RemoteTask> = emptyList(),
    val archives: List<ArchiveItem> = emptyList(),
    val openingArchive: ArchiveItem? = null,
    val archiveContent: ArchiveContent? = null,
    val isTesting: Boolean = false,
    val isSubmitting: Boolean = false,
    val isRefreshing: Boolean = false,
    val isLoadingLibrary: Boolean = false,
    val isLoadingArchive: Boolean = false,
    val libraryError: String = "",
    val archiveError: String = "",
    val isPolishing: Boolean = false,
    val polishError: String = "",
    val notice: String = "",
    val noticeIsError: Boolean = false,
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val processingTarget: ProcessingTarget = ProcessingTarget.SERVER,
) {
    val isConfigured: Boolean
        get() = baseUrl.isNotBlank()
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val configStore = SecureConfigStore(application)
    private val recentTaskStore = RecentTaskStore(application)

    private val savedConfig = configStore.load()
    private val _uiState = MutableStateFlow(
        MainUiState(
            baseUrl = savedConfig.baseUrl,
            apiToken = savedConfig.apiToken,
            recentTasks = recentTaskStore.load(),
            themeMode = configStore.loadThemeMode(),
            processingTarget = configStore.loadProcessingTarget(),
        ),
    )
    val uiState: StateFlow<MainUiState> = _uiState.asStateFlow()

    init {
        if (savedConfig.baseUrl.isNotBlank()) {
            refreshLibrary()
            refreshTasks(showNotice = false)
        }
    }

    fun updateBaseUrl(value: String) {
        _uiState.update { it.copy(baseUrl = value, notice = "") }
    }

    fun updateApiToken(value: String) {
        _uiState.update { it.copy(apiToken = value, notice = "") }
    }

    fun updateSharedText(value: String) {
        _uiState.update { it.copy(sharedText = value, notice = "") }
    }

    fun updateThemeMode(value: ThemeMode) {
        configStore.saveThemeMode(value)
        _uiState.update { it.copy(themeMode = value) }
    }

    fun updateProcessingTarget(value: ProcessingTarget) {
        configStore.saveProcessingTarget(value)
        _uiState.update { it.copy(processingTarget = value) }
    }

    fun receiveSharedText(value: String) {
        if (value.isBlank()) return
        _uiState.update {
            it.copy(
                sharedText = value.trim(),
                notice = "已接收分享内容，请确认后提交。",
                noticeIsError = false,
            )
        }
    }

    fun saveSettings() {
        val config = validatedConfig() ?: return
        if (!persistConfig(config)) return
        _uiState.update {
            it.copy(
                baseUrl = config.baseUrl,
                notice = "服务器配置已安全保存，内容库正在同步。",
                noticeIsError = false,
            )
        }
        refreshLibrary()
        refreshTasks(showNotice = false)
    }

    fun testConnection() {
        val config = validatedConfig() ?: return
        if (!persistConfig(config)) return
        _uiState.update { it.copy(isTesting = true, notice = "") }

        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    MppApiClient(config).testConnection()
                }
            }
            _uiState.update {
                if (result.isSuccess) {
                    it.copy(
                        baseUrl = config.baseUrl,
                        isTesting = false,
                        notice = "连接成功，远程内容库可以访问。",
                        noticeIsError = false,
                    )
                } else {
                    it.copy(
                        isTesting = false,
                        notice = result.exceptionOrNull().toUserMessage(),
                        noticeIsError = true,
                    )
                }
            }
            if (result.isSuccess) {
                refreshLibrary()
                refreshTasks(showNotice = false)
            }
        }
    }

    fun submit() {
        val state = _uiState.value
        val source = state.sharedText.trim()
        if (source.isBlank()) {
            showError("请粘贴链接，或从其他 App 分享到 MPP Remote。")
            return
        }
        val config = validatedConfig() ?: return
        if (!persistConfig(config)) return
        _uiState.update { it.copy(isSubmitting = true, notice = "") }

        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    MppApiClient(config).createTask(
                        source = source,
                        requestedExecutor = state.processingTarget,
                    )
                }
            }
            val task = result.getOrNull()
            if (task != null) {
                val tasks = recentTaskStore.upsert(task)
                _uiState.update {
                    it.copy(
                        baseUrl = config.baseUrl,
                        sharedText = "",
                        recentTasks = tasks,
                        isSubmitting = false,
                        notice = "任务已进入远程处理队列，完成后会出现在内容库。",
                        noticeIsError = false,
                    )
                }
            } else {
                _uiState.update {
                    it.copy(
                        isSubmitting = false,
                        notice = result.exceptionOrNull().toUserMessage(),
                        noticeIsError = true,
                    )
                }
            }
        }
    }

    fun refreshLibrary() {
        val config = currentConfigOrNull()
        if (config == null) {
            _uiState.update {
                it.copy(
                    isLoadingLibrary = false,
                    libraryError = "请先在设置中配置远程服务器。",
                )
            }
            return
        }
        if (_uiState.value.isLoadingLibrary) return
        _uiState.update { it.copy(isLoadingLibrary = true, libraryError = "") }

        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) { MppApiClient(config).listArchives() }
            }
            _uiState.update {
                if (result.isSuccess) {
                    it.copy(
                        archives = result.getOrDefault(emptyList()),
                        isLoadingLibrary = false,
                        libraryError = "",
                    )
                } else {
                    it.copy(
                        isLoadingLibrary = false,
                        libraryError = result.exceptionOrNull().toUserMessage(),
                    )
                }
            }
        }
    }

    fun openArchive(item: ArchiveItem) {
        val config = currentConfigOrNull() ?: return
        _uiState.update {
            it.copy(
                openingArchive = item,
                archiveContent = null,
                isLoadingArchive = true,
                archiveError = "",
                isPolishing = false,
                polishError = "",
            )
        }
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    MppApiClient(config).getArchiveContent(item)
                }
            }
            _uiState.update {
                if (result.isSuccess) {
                    it.copy(
                        openingArchive = result.getOrThrow().archive,
                        archiveContent = result.getOrThrow(),
                        isLoadingArchive = false,
                    )
                } else {
                    it.copy(
                        isLoadingArchive = false,
                        archiveError = result.exceptionOrNull().toUserMessage(),
                    )
                }
            }
        }
    }

    fun closeArchive() {
        _uiState.update {
            it.copy(
                openingArchive = null,
                archiveContent = null,
                isLoadingArchive = false,
                archiveError = "",
                isPolishing = false,
                polishError = "",
            )
        }
    }

    fun refreshTasks(showNotice: Boolean = true) {
        val config = currentConfigOrNull() ?: return
        if (_uiState.value.isRefreshing) return
        _uiState.update { it.copy(isRefreshing = true, notice = if (showNotice) "" else it.notice) }

        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) { MppApiClient(config).listTasks() }
            }
            val refreshed = result.getOrNull()
            if (refreshed != null) recentTaskStore.save(refreshed)
            _uiState.update {
                if (refreshed != null) {
                    it.copy(
                        recentTasks = refreshed,
                        isRefreshing = false,
                        notice = if (showNotice) "远程任务状态已更新。" else it.notice,
                        noticeIsError = false,
                    )
                } else {
                    it.copy(
                        isRefreshing = false,
                        notice = if (showNotice) {
                            result.exceptionOrNull().toUserMessage()
                        } else {
                            it.notice
                        },
                        noticeIsError = showNotice,
                    )
                }
            }
        }
    }

    suspend fun loadThumbnail(path: String): ByteArray? {
        val config = currentConfigOrNull() ?: return null
        return runCatching {
            withContext(Dispatchers.IO) {
                MppApiClient(config).loadThumbnail(path)
            }
        }.getOrNull()
    }

    suspend fun loadArchiveImage(path: String, maxEdge: Int): ByteArray? {
        val config = currentConfigOrNull() ?: return null
        return runCatching {
            withContext(Dispatchers.IO) {
                MppApiClient(config).loadArchiveImage(path, maxEdge)
            }
        }.getOrNull()
    }

    fun polishTranscript() {
        val state = _uiState.value
        val content = state.archiveContent ?: return
        val input = content.transcriptSrt.ifBlank { content.transcript }
        if (input.isBlank()) {
            _uiState.update { it.copy(polishError = "当前内容没有可润色的字幕。") }
            return
        }
        if (state.isPolishing) return
        val config = currentConfigOrNull() ?: return
        val archivePath = content.archive.path
        _uiState.update { it.copy(isPolishing = true, polishError = "") }

        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    MppApiClient(config).polishArchive(archivePath, input).also {
                        require(it.isNotBlank()) { "服务器未返回润色内容" }
                    }
                }
            }
            _uiState.update { current ->
                if (current.archiveContent?.archive?.path != archivePath) {
                    current.copy(isPolishing = false)
                } else if (result.isSuccess) {
                    current.copy(
                        archiveContent = current.archiveContent.copy(
                            extraPolish = result.getOrThrow(),
                        ),
                        isPolishing = false,
                        polishError = "",
                    )
                } else {
                    current.copy(
                        isPolishing = false,
                        polishError = result.exceptionOrNull().toUserMessage(),
                    )
                }
            }
        }
    }

    private fun validatedConfig(): ServerConfig? {
        val state = _uiState.value
        val normalizedUrl = runCatching { normalizeServerUrl(state.baseUrl) }
            .getOrElse {
                showError(it.message ?: "服务器地址无效")
                return null
            }
        return ServerConfig(
            baseUrl = normalizedUrl,
            apiToken = state.apiToken.trim(),
        )
    }

    private fun currentConfigOrNull(): ServerConfig? {
        val state = _uiState.value
        val normalizedUrl = runCatching { normalizeServerUrl(state.baseUrl) }.getOrNull()
            ?: return null
        return ServerConfig(normalizedUrl, state.apiToken.trim())
    }

    private fun showError(message: String) {
        _uiState.update { it.copy(notice = message, noticeIsError = true) }
    }

    private fun persistConfig(config: ServerConfig): Boolean {
        val result = runCatching { configStore.save(config) }
        if (result.isFailure) {
            showError("无法安全保存 API Token：${result.exceptionOrNull()?.message.orEmpty()}")
        }
        return result.isSuccess
    }
}

private fun Throwable?.toUserMessage(): String = when (this) {
    null -> "操作失败"
    is java.net.ConnectException -> "无法连接服务器，请检查地址、后端监听地址和防火墙。"
    is java.net.SocketTimeoutException -> "连接超时，请检查网络和服务器状态。"
    is java.net.UnknownHostException -> "找不到服务器，请检查域名或网络连接。"
    else -> message ?: "操作失败"
}
