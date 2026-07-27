package com.mpp.remote.ui

import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.*
import androidx.compose.material.icons.outlined.*
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveDocument
import com.mpp.remote.data.ArchiveItem
import com.mpp.remote.data.RemoteTask
import com.mpp.remote.data.ThemeMode
import kotlin.math.roundToInt

private enum class AppDestination(
    val label: String,
    val icon: ImageVector,
) {
    LIBRARY("内容库", Icons.Outlined.FolderOpen),
    SUBMIT("投递", Icons.Outlined.AddCircleOutline),
    TASKS("任务", Icons.Outlined.TaskAlt),
    SETTINGS("设置", Icons.Outlined.Settings),
}

private enum class LibraryFilter(
    val label: String,
    val icon: ImageVector,
) {
    ALL("全部", Icons.Outlined.GridView),
    VIDEO("视频", Icons.Outlined.VideoLibrary),
    ARTICLE("网页", Icons.AutoMirrored.Outlined.Article),
    AUDIO("音频", Icons.Outlined.AudioFile),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MppRemoteApp(viewModel: MainViewModel) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    var destination by rememberSaveable { mutableStateOf(AppDestination.LIBRARY) }

    LaunchedEffect(state.sharedText) {
        if (state.sharedText.isNotBlank()) destination = AppDestination.SUBMIT
    }

    LaunchedEffect(destination, state.isConfigured) {
        if (!state.isConfigured) return@LaunchedEffect
        when (destination) {
            AppDestination.LIBRARY -> viewModel.refreshLibrary()
            AppDestination.TASKS -> viewModel.refreshTasks(showNotice = false)
            else -> Unit
        }
    }

    if (state.openingArchive != null) {
        ArchiveReader(
            state = state,
            onBack = viewModel::closeArchive,
            onRetry = { state.openingArchive?.let(viewModel::openArchive) },
            loadThumbnail = viewModel::loadThumbnail,
        )
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(destination.label, fontWeight = FontWeight.SemiBold)
                        if (destination == AppDestination.LIBRARY) {
                            Text(
                                "远程处理结果会同步到这里",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                actions = {
                    if (destination == AppDestination.LIBRARY) {
                        TextButton(
                            onClick = viewModel::refreshLibrary,
                            enabled = !state.isLoadingLibrary,
                        ) {
                            if (state.isLoadingLibrary) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    strokeWidth = 2.dp,
                                )
                            } else {
                                Icon(
                                    Icons.Outlined.Refresh,
                                    contentDescription = null,
                                    modifier = Modifier.size(18.dp),
                                )
                            }
                            Spacer(Modifier.width(6.dp))
                            Text(if (state.isLoadingLibrary) "同步中…" else "同步")
                        }
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                AppDestination.entries.forEach { item ->
                    NavigationBarItem(
                        selected = destination == item,
                        onClick = { destination = item },
                        icon = {
                            Icon(
                                imageVector = item.icon,
                                contentDescription = item.label,
                            )
                        },
                        label = { Text(item.label) },
                    )
                }
            }
        },
    ) { contentPadding ->
        when (destination) {
            AppDestination.LIBRARY -> LibraryScreen(
                state = state,
                modifier = Modifier.padding(contentPadding),
                onRefresh = viewModel::refreshLibrary,
                onOpenArchive = viewModel::openArchive,
                onOpenSettings = { destination = AppDestination.SETTINGS },
                loadThumbnail = viewModel::loadThumbnail,
            )

            AppDestination.SUBMIT -> SubmitScreen(
                state = state,
                modifier = Modifier.padding(contentPadding),
                onTextChange = viewModel::updateSharedText,
                onSubmit = viewModel::submit,
            )

            AppDestination.TASKS -> TasksScreen(
                state = state,
                modifier = Modifier.padding(contentPadding),
                onRefresh = { viewModel.refreshTasks() },
            )

            AppDestination.SETTINGS -> SettingsScreen(
                state = state,
                modifier = Modifier.padding(contentPadding),
                onBaseUrlChange = viewModel::updateBaseUrl,
                onTokenChange = viewModel::updateApiToken,
                onSave = viewModel::saveSettings,
                onTest = viewModel::testConnection,
                onThemeModeChange = viewModel::updateThemeMode,
            )
        }
    }
}

@Composable
private fun LibraryScreen(
    state: MainUiState,
    modifier: Modifier,
    onRefresh: () -> Unit,
    onOpenArchive: (ArchiveItem) -> Unit,
    onOpenSettings: () -> Unit,
    loadThumbnail: suspend (String) -> ByteArray?,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf(LibraryFilter.ALL) }
    val filtered = remember(state.archives, query, filter) {
        val keyword = query.trim()
        state.archives.filter { archive ->
            val matchesQuery = keyword.isBlank() ||
                archive.title.contains(keyword, ignoreCase = true) ||
                archive.uploader.contains(keyword, ignoreCase = true) ||
                archive.keywords.any { it.contains(keyword, ignoreCase = true) }
            val matchesFilter = when (filter) {
                LibraryFilter.ALL -> true
                LibraryFilter.VIDEO -> archive.hasVideo
                LibraryFilter.ARTICLE -> archive.contentSubtype in setOf("text_note", "article", "image_note")
                LibraryFilter.AUDIO -> archive.hasAudio
            }
            matchesQuery && matchesFilter
        }
    }

    Column(modifier = modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            placeholder = { Text("搜索标题、作者或关键词") },
            leadingIcon = {
                Icon(Icons.Outlined.Search, contentDescription = null)
            },
            singleLine = true,
        )
        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(LibraryFilter.entries) { item ->
                FilterChip(
                    selected = filter == item,
                    onClick = { filter = item },
                    label = { Text(item.label) },
                    leadingIcon = {
                        Icon(
                            imageVector = item.icon,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                    },
                )
            }
        }

        when {
            state.isLoadingLibrary && state.archives.isEmpty() -> {
                CenterMessage {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(14.dp))
                    Text("正在同步远程内容库")
                }
            }

            state.libraryError.isNotBlank() && state.archives.isEmpty() -> {
                CenterMessage {
                    Text(
                        state.libraryError,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(14.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedButton(onClick = onOpenSettings) {
                            Icon(
                                Icons.Outlined.Settings,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(6.dp))
                            Text("打开设置")
                        }
                        Button(onClick = onRefresh) {
                            Icon(
                                Icons.Outlined.Refresh,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(6.dp))
                            Text("重试")
                        }
                    }
                }
            }

            filtered.isEmpty() -> {
                CenterMessage {
                    Text(
                        if (state.archives.isEmpty()) {
                            "服务器中还没有处理完成的内容"
                        } else {
                            "没有找到匹配内容"
                        },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            else -> {
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(minSize = 270.dp),
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    items(filtered, key = { it.path }) { archive ->
                        ArchiveCard(
                            archive = archive,
                            onClick = { onOpenArchive(archive) },
                            loadThumbnail = loadThumbnail,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ArchiveCard(
    archive: ArchiveItem,
    onClick: () -> Unit,
    loadThumbnail: suspend (String) -> ByteArray?,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainer,
        ),
    ) {
        ArchiveThumbnail(
            archive = archive,
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(16f / 9f),
            loadThumbnail = loadThumbnail,
        )
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    platformLabel(archive),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                if (archive.processing) {
                    Text(
                        "处理中",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.tertiary,
                    )
                }
            }
            Text(
                displayArchiveTitle(archive),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                listOf(archive.uploader, formatDate(archive.createdAt))
                    .filter(String::isNotBlank)
                    .joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

private val uuidTitlePattern =
    Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

private fun displayArchiveTitle(archive: ArchiveItem): String =
    if (archive.processing && uuidTitlePattern.matches(archive.title)) {
        "正在识别内容"
    } else {
        archive.title
    }

@Composable
private fun ArchiveThumbnail(
    archive: ArchiveItem,
    modifier: Modifier,
    loadThumbnail: suspend (String) -> ByteArray?,
) {
    var imageBytes by remember(archive.path) { mutableStateOf<ByteArray?>(null) }
    var loaded by remember(archive.path) { mutableStateOf(false) }

    LaunchedEffect(archive.path) {
        imageBytes = loadThumbnail(archive.path)
        loaded = true
    }

    val bitmap = remember(imageBytes) {
        imageBytes?.let { bytes ->
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        }
    }

    Box(
        modifier = modifier,
        contentAlignment = Alignment.Center,
    ) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = archive.title,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        } else {
            Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.primaryContainer,
            ) {
                Box(contentAlignment = Alignment.Center) {
                    if (!loaded) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(24.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Icon(
                                imageVector = when {
                                    archive.hasVideo -> Icons.Outlined.VideoLibrary
                                    archive.hasAudio -> Icons.Outlined.AudioFile
                                    else -> Icons.AutoMirrored.Outlined.Article
                                },
                                contentDescription = null,
                                modifier = Modifier.size(38.dp),
                                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                            Text(
                                platformLabel(archive),
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ArchiveReader(
    state: MainUiState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    loadThumbnail: suspend (String) -> ByteArray?,
) {
    val seed = state.openingArchive ?: return
    val content = state.archiveContent
    var selectedDocument by rememberSaveable(seed.path) {
        mutableStateOf<ArchiveDocument?>(null)
    }

    LaunchedEffect(content?.archive?.path) {
        if (content != null) selectedDocument = content.initialDocument
    }
    BackHandler(onBack = onBack)

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        seed.title,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Outlined.ArrowBack,
                            contentDescription = "返回",
                        )
                    }
                },
            )
        },
    ) { contentPadding ->
        when {
            state.isLoadingArchive -> {
                CenterMessage(modifier = Modifier.padding(contentPadding)) {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(14.dp))
                    Text("正在加载处理结果")
                }
            }

            content == null -> {
                CenterMessage(modifier = Modifier.padding(contentPadding)) {
                    Text(
                        state.archiveError.ifBlank { "内容加载失败" },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(14.dp))
                    Button(onClick = onRetry) {
                        Icon(
                            Icons.Outlined.Refresh,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text("重试")
                    }
                }
            }

            else -> {
                val documents = ArchiveDocument.entries.filter {
                    content.contentFor(it).isNotBlank()
                }
                val activeDocument = selectedDocument?.takeIf(documents::contains)
                    ?: content.initialDocument
                val activeContent = content.contentFor(activeDocument)
                val uriHandler = LocalUriHandler.current

                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(contentPadding),
                ) {
                    LazyColumn(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                        contentPadding = PaddingValues(bottom = 28.dp),
                    ) {
                        item {
                            Box(
                                modifier = Modifier.fillMaxWidth(),
                                contentAlignment = Alignment.Center,
                            ) {
                                ArchiveThumbnail(
                                    archive = content.archive,
                                    modifier = Modifier
                                        .widthIn(max = 720.dp)
                                        .fillMaxWidth()
                                        .aspectRatio(16f / 9f),
                                    loadThumbnail = loadThumbnail,
                                )
                            }
                        }
                        item {
                            Column(
                                modifier = Modifier.padding(18.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Text(
                                    content.archive.title,
                                    style = MaterialTheme.typography.headlineSmall,
                                    fontWeight = FontWeight.Bold,
                                )
                                Text(
                                    listOf(
                                        content.archive.uploader,
                                        formatDate(content.archive.createdAt),
                                        formatDuration(content.archive.durationSeconds),
                                    ).filter(String::isNotBlank).joinToString(" · "),
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (content.archive.topics.isNotEmpty()) {
                                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        items(content.archive.topics.take(6)) { topic ->
                                            AssistChip(
                                                onClick = {},
                                                label = { Text(topic) },
                                            )
                                        }
                                    }
                                }
                                if (content.archive.sourceUrl.isNotBlank()) {
                                    OutlinedButton(
                                        onClick = {
                                            runCatching {
                                                uriHandler.openUri(content.archive.sourceUrl)
                                            }
                                        },
                                    ) {
                                        Icon(
                                            Icons.AutoMirrored.Outlined.OpenInNew,
                                            contentDescription = null,
                                            modifier = Modifier.size(18.dp),
                                        )
                                        Spacer(Modifier.width(6.dp))
                                        Text("打开原始链接")
                                    }
                                }
                            }
                        }
                        item { HorizontalDivider() }
                        item {
                            LazyRow(
                                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                items(documents) { document ->
                                    FilterChip(
                                        selected = activeDocument == document,
                                        onClick = { selectedDocument = document },
                                        label = { Text(document.label) },
                                        leadingIcon = {
                                            Icon(
                                                imageVector = documentIcon(document),
                                                contentDescription = null,
                                                modifier = Modifier.size(18.dp),
                                            )
                                        },
                                    )
                                }
                            }
                        }
                        markdownItems(activeContent)
                    }
                }
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.markdownItems(content: String) {
    val lines = prepareMarkdownLines(content)
    itemsIndexed(lines) { _, line ->
        val trimmed = line.trim()
        when {
            trimmed.isBlank() -> Spacer(Modifier.height(10.dp))
            trimmed.startsWith("# ") -> ReaderText(
                text = cleanMarkdown(trimmed.removePrefix("# ")),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
            )

            trimmed.startsWith("## ") -> ReaderText(
                text = cleanMarkdown(trimmed.removePrefix("## ")),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
            )

            trimmed.startsWith("### ") -> ReaderText(
                text = cleanMarkdown(trimmed.removePrefix("### ")),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )

            trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                Row(
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Text("•", modifier = Modifier.width(20.dp))
                    SelectionContainer {
                        Text(
                            cleanMarkdown(trimmed.drop(2)),
                            style = MaterialTheme.typography.bodyLarge,
                            lineHeight = MaterialTheme.typography.bodyLarge.lineHeight * 1.25,
                        )
                    }
                }
            }

            trimmed.startsWith("> ") -> {
                Surface(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 5.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerHighest,
                    shape = MaterialTheme.shapes.small,
                ) {
                    SelectionContainer {
                        Text(
                            cleanMarkdown(trimmed.removePrefix("> ")),
                            modifier = Modifier.padding(14.dp),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            trimmed.startsWith("```") -> Unit
            else -> ReaderText(
                text = cleanMarkdown(trimmed),
                style = MaterialTheme.typography.bodyLarge,
            )
        }
    }
}

@Composable
private fun ReaderText(
    text: String,
    style: androidx.compose.ui.text.TextStyle,
    fontWeight: FontWeight? = null,
) {
    SelectionContainer {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 5.dp),
            style = style,
            fontWeight = fontWeight,
            lineHeight = style.lineHeight * 1.25,
        )
    }
}

@Composable
private fun SubmitScreen(
    state: MainUiState,
    modifier: Modifier,
    onTextChange: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
            ) {
                Column(
                    modifier = Modifier.padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        "发送到远程处理",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "在 B站、小红书、YouTube、播客等 App 中点击分享并选择 MPP，链接会自动填入。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    OutlinedTextField(
                        value = state.sharedText,
                        onValueChange = onTextChange,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("链接或分享文案") },
                        placeholder = { Text("也可以直接粘贴链接") },
                        leadingIcon = {
                            Icon(Icons.Outlined.Link, contentDescription = null)
                        },
                        minLines = 5,
                        maxLines = 12,
                    )
                    Button(
                        onClick = onSubmit,
                        enabled = !state.isSubmitting && state.sharedText.isNotBlank(),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (state.isSubmitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                            )
                            Spacer(Modifier.width(8.dp))
                        } else {
                            Icon(
                                Icons.AutoMirrored.Outlined.Send,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text(if (state.isSubmitting) "正在提交" else "提交到远程队列")
                    }
                }
            }
        }
        if (state.notice.isNotBlank()) {
            item { NoticeCard(state.notice, state.noticeIsError) }
        }
    }
}

@Composable
private fun TasksScreen(
    state: MainUiState,
    modifier: Modifier,
    onRefresh: () -> Unit,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        "远程任务",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "显示服务器最近 50 个任务",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onRefresh, enabled = !state.isRefreshing) {
                    if (state.isRefreshing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(
                            Icons.Outlined.Refresh,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    Spacer(Modifier.width(6.dp))
                    Text(if (state.isRefreshing) "刷新中…" else "刷新")
                }
            }
        }
        if (state.notice.isNotBlank()) {
            item { NoticeCard(state.notice, state.noticeIsError) }
        }
        if (state.recentTasks.isEmpty()) {
            item {
                Text(
                    "服务器中还没有任务。",
                    modifier = Modifier.padding(vertical = 30.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            items(state.recentTasks, key = { it.id }) { task ->
                TaskCard(task)
            }
        }
    }
}

@Composable
private fun SettingsScreen(
    state: MainUiState,
    modifier: Modifier,
    onBaseUrlChange: (String) -> Unit,
    onTokenChange: (String) -> Unit,
    onSave: () -> Unit,
    onTest: () -> Unit,
    onThemeModeChange: (ThemeMode) -> Unit,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            AppearanceCard(
                themeMode = state.themeMode,
                onThemeModeChange = onThemeModeChange,
            )
        }
        item {
            ServerCard(
                state = state,
                onBaseUrlChange = onBaseUrlChange,
                onTokenChange = onTokenChange,
                onSave = onSave,
                onTest = onTest,
            )
        }
        if (state.notice.isNotBlank()) {
            item { NoticeCard(state.notice, state.noticeIsError) }
        }
        item {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceContainer,
                ),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    SectionTitle(
                        icon = Icons.Outlined.Info,
                        title = "连接说明",
                        compact = true,
                    )
                    Text(
                        "MuMu + adb reverse： http://localhost:18000\n" +
                            "同一 Wi-Fi：填写电脑局域网地址\n" +
                            "远程访问：填写服务器 HTTPS 域名",
                        style = MaterialTheme.typography.bodyMedium,
                        fontFamily = FontFamily.SansSerif,
                    )
                }
            }
        }
    }
}

@Composable
private fun AppearanceCard(
    themeMode: ThemeMode,
    onThemeModeChange: (ThemeMode) -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionTitle(Icons.Outlined.Palette, "外观")
            Text(
                "选择适合当前环境的显示模式，切换后立即生效。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                items(ThemeMode.entries) { mode ->
                    FilterChip(
                        selected = themeMode == mode,
                        onClick = { onThemeModeChange(mode) },
                        label = {
                            Text(
                                when (mode) {
                                    ThemeMode.SYSTEM -> "跟随系统"
                                    ThemeMode.LIGHT -> "浅色"
                                    ThemeMode.DARK -> "深色"
                                },
                            )
                        },
                        leadingIcon = {
                            Icon(
                                imageVector = when (mode) {
                                    ThemeMode.SYSTEM -> Icons.Outlined.BrightnessAuto
                                    ThemeMode.LIGHT -> Icons.Outlined.LightMode
                                    ThemeMode.DARK -> Icons.Outlined.DarkMode
                                },
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun ServerCard(
    state: MainUiState,
    onBaseUrlChange: (String) -> Unit,
    onTokenChange: (String) -> Unit,
    onSave: () -> Unit,
    onTest: () -> Unit,
) {
    var showToken by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionTitle(Icons.Outlined.Dns, "远程服务器")
            Text(
                "连接后，网页端已经处理完成的内容会显示在手机内容库中。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = state.baseUrl,
                onValueChange = onBaseUrlChange,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Server URL") },
                placeholder = { Text("https://mpp.example.com") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            OutlinedTextField(
                value = state.apiToken,
                onValueChange = onTokenChange,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("API Token") },
                singleLine = true,
                visualTransformation = if (showToken) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                trailingIcon = {
                    TextButton(onClick = { showToken = !showToken }) {
                        Text(if (showToken) "隐藏" else "显示")
                    }
                },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = onSave, enabled = !state.isTesting) {
                    Icon(
                        Icons.Outlined.Save,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("保存并同步")
                }
                Button(onClick = onTest, enabled = !state.isTesting) {
                    if (state.isTesting) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                        Spacer(Modifier.width(8.dp))
                    } else {
                        Icon(
                            Icons.Outlined.CheckCircle,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text("测试连接")
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(
    icon: ImageVector,
    title: String,
    compact: Boolean = false,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            modifier = Modifier.size(if (compact) 20.dp else 24.dp),
            tint = MaterialTheme.colorScheme.primary,
        )
        Text(
            title,
            style = if (compact) {
                MaterialTheme.typography.titleMedium
            } else {
                MaterialTheme.typography.titleLarge
            },
            fontWeight = FontWeight.SemiBold,
        )
    }
}

private fun documentIcon(document: ArchiveDocument): ImageVector =
    when (document) {
        ArchiveDocument.SUMMARY -> Icons.Outlined.Description
        ArchiveDocument.SOURCE -> Icons.Outlined.Language
        ArchiveDocument.TRANSCRIPT -> Icons.Outlined.Subtitles
        ArchiveDocument.MINDMAP -> Icons.Outlined.AccountTree
        ArchiveDocument.DETAIL -> Icons.Outlined.Info
    }

@Composable
private fun NoticeCard(message: String, isError: Boolean) {
    val containerColor = if (isError) {
        MaterialTheme.colorScheme.errorContainer
    } else {
        MaterialTheme.colorScheme.secondaryContainer
    }
    val contentColor = if (isError) {
        MaterialTheme.colorScheme.onErrorContainer
    } else {
        MaterialTheme.colorScheme.onSecondaryContainer
    }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = containerColor,
        contentColor = contentColor,
        shape = MaterialTheme.shapes.medium,
    ) {
        Text(
            text = message,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun TaskCard(task: RemoteTask) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                StatusBadge(task.status)
                Text(
                    formatDate(task.createdAt),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                task.source,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodyMedium,
            )
            if (task.status in setOf("queued", "pending", "processing", "paused")) {
                LinearProgressIndicator(
                    progress = { task.progress.coerceIn(0.0, 1.0).toFloat() },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "${task.progressPercent}%  ${task.message}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            } else if (task.message.isNotBlank()) {
                HorizontalDivider()
                Text(
                    task.message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun StatusBadge(status: String) {
    val (label, color) = when (status) {
        "queued", "pending" -> "排队中" to MaterialTheme.colorScheme.secondaryContainer
        "processing" -> "处理中" to MaterialTheme.colorScheme.primaryContainer
        "paused" -> "已暂停" to MaterialTheme.colorScheme.tertiaryContainer
        "completed" -> "已完成" to MaterialTheme.colorScheme.primaryContainer
        "failed" -> "失败" to MaterialTheme.colorScheme.errorContainer
        "cancelled" -> "已取消" to MaterialTheme.colorScheme.surfaceVariant
        else -> status to MaterialTheme.colorScheme.surfaceVariant
    }
    Surface(color = color, shape = MaterialTheme.shapes.small) {
        Text(
            label,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun CenterMessage(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            content = content,
        )
    }
}

private fun platformLabel(archive: ArchiveItem): String = when {
    archive.platform.startsWith("bilibili") -> "B站"
    archive.platform == "youtube" -> "YouTube"
    archive.platform == "xiaohongshu" -> "小红书"
    archive.platform == "webpage" -> "网页"
    archive.hasAudio -> "音频"
    archive.hasVideo -> "视频"
    else -> "内容"
}

private fun formatDate(value: String): String =
    value.replace('T', ' ').take(16)

private fun formatDuration(seconds: Double?): String {
    if (seconds == null || seconds <= 0) return ""
    val total = seconds.roundToInt()
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    return if (hours > 0) "${hours}小时${minutes}分" else "${minutes}分钟"
}

private fun prepareMarkdownLines(content: String): List<String> {
    val lines = content.lines()
    if (lines.firstOrNull()?.trim() != "---") return lines
    val end = lines.drop(1).indexOfFirst { it.trim() == "---" }
    return if (end >= 0) lines.drop(end + 2) else lines
}

private fun cleanMarkdown(value: String): String =
    value
        .replace(Regex("""!\[([^\]]*)]\([^)]+\)"""), "$1")
        .replace(Regex("""\[([^\]]+)]\(([^)]+)\)"""), "$1")
        .replace("**", "")
        .replace("__", "")
        .replace("`", "")
