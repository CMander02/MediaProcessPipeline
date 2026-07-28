package com.mpp.remote.ui

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountTree
import androidx.compose.material.icons.outlined.FitScreen
import androidx.compose.material.icons.outlined.Notes
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlin.math.max
import kotlin.math.min

internal data class MindmapNode(
    val id: Int,
    val text: String,
    val depth: Int,
    val parentId: Int?,
)

internal data class PositionedMindmapNode(
    val node: MindmapNode,
    val x: Float,
    val y: Float,
)

internal data class MindmapLayoutResult(
    val nodes: List<PositionedMindmapNode>,
    val width: Float,
    val height: Float,
)

private const val MINDMAP_NODE_WIDTH = 196f
private const val MINDMAP_NODE_HEIGHT = 62f
private const val MINDMAP_HORIZONTAL_GAP = 64f
private const val MINDMAP_VERTICAL_GAP = 26f

internal fun parseMindmapMarkdown(
    markdown: String,
    fallbackTitle: String = "思维导图",
): List<MindmapNode> {
    val headingPattern = Regex("""^(#{1,6})\s+(.+)$""")
    val bulletPattern = Regex("""^(\s*)(?:[-*+]|\d+[.)])\s+(.+)$""")
    val lines = markdown
        .replace("\r\n", "\n")
        .replace('\r', '\n')
        .lines()
    val rootHeadingIndex = lines.indexOfFirst { line ->
        headingPattern.matchEntire(line.trim())?.groupValues?.get(1)?.length == 1
    }
    val rootTitle = rootHeadingIndex
        .takeIf { it >= 0 }
        ?.let { headingPattern.matchEntire(lines[it].trim())?.groupValues?.get(2) }
        ?.let(::cleanMindmapText)
        .orEmpty()
        .ifBlank { fallbackTitle.ifBlank { "思维导图" } }

    val nodes = mutableListOf(
        MindmapNode(id = 0, text = rootTitle, depth = 0, parentId = null),
    )
    val stack = mutableMapOf(0 to 0)
    var nextId = 1
    var inCodeFence = false

    lines.forEachIndexed { index, rawLine ->
        val trimmed = rawLine.trim()
        if (trimmed.startsWith("```")) {
            inCodeFence = !inCodeFence
            return@forEachIndexed
        }
        if (trimmed.isBlank() || trimmed.equals("mindmap", ignoreCase = true)) {
            return@forEachIndexed
        }
        if (index == rootHeadingIndex) return@forEachIndexed

        val heading = headingPattern.matchEntire(trimmed)
        val bullet = bulletPattern.matchEntire(rawLine.replace("\t", "  "))
        val (depth, rawText) = when {
            heading != null -> {
                val headingDepth = max(1, heading.groupValues[1].length - 1)
                headingDepth to heading.groupValues[2]
            }

            bullet != null -> {
                val indentDepth = bullet.groupValues[1].length / 2
                (indentDepth + 1) to bullet.groupValues[2]
            }

            inCodeFence || rawLine.firstOrNull()?.isWhitespace() == true -> {
                val indent = rawLine.takeWhile(Char::isWhitespace).length / 2
                (indent + 1) to trimmed
            }

            else -> 1 to trimmed
        }
        val text = cleanMindmapText(rawText)
        if (text.isBlank()) return@forEachIndexed

        val parentDepth = stack.keys.filter { it < depth }.maxOrNull() ?: 0
        val parentId = stack[parentDepth] ?: 0
        val node = MindmapNode(
            id = nextId++,
            text = text,
            depth = depth,
            parentId = parentId,
        )
        nodes += node
        stack.keys.filter { it >= depth }.toList().forEach(stack::remove)
        stack[depth] = node.id
    }
    return nodes
}

internal fun layoutMindmap(nodes: List<MindmapNode>): MindmapLayoutResult {
    if (nodes.isEmpty()) return MindmapLayoutResult(emptyList(), 0f, 0f)
    val byId = nodes.associateBy(MindmapNode::id)
    val children = nodes
        .filter { it.parentId != null }
        .groupBy { it.parentId }
    val positions = mutableMapOf<Int, Float>()
    var nextLeafCenter = MINDMAP_NODE_HEIGHT / 2f

    fun place(nodeId: Int): Float {
        positions[nodeId]?.let { return it }
        val childNodes = children[nodeId].orEmpty()
        val center = if (childNodes.isEmpty()) {
            val value = nextLeafCenter
            nextLeafCenter += MINDMAP_NODE_HEIGHT + MINDMAP_VERTICAL_GAP
            value
        } else {
            childNodes.map { place(it.id) }.average().toFloat()
        }
        positions[nodeId] = center
        return center
    }

    nodes.filter { it.parentId == null || it.parentId !in byId }.forEach { place(it.id) }
    nodes.forEach { place(it.id) }
    val positioned = nodes.map { node ->
        PositionedMindmapNode(
            node = node,
            x = node.depth * (MINDMAP_NODE_WIDTH + MINDMAP_HORIZONTAL_GAP),
            y = (positions[node.id] ?: MINDMAP_NODE_HEIGHT / 2f) - MINDMAP_NODE_HEIGHT / 2f,
        )
    }
    val maxDepth = nodes.maxOfOrNull(MindmapNode::depth) ?: 0
    val width = (maxDepth + 1) * MINDMAP_NODE_WIDTH + maxDepth * MINDMAP_HORIZONTAL_GAP
    val height = max(
        MINDMAP_NODE_HEIGHT,
        positioned.maxOfOrNull { it.y + MINDMAP_NODE_HEIGHT } ?: MINDMAP_NODE_HEIGHT,
    )
    return MindmapLayoutResult(positioned, width, height)
}

private fun cleanMindmapText(value: String): String =
    value
        .replace(Regex("""!\[([^\]]*)]\([^)]*\)"""), "$1")
        .replace(Regex("""\[([^\]]+)]\([^)]*\)"""), "$1")
        .replace(Regex("""[*_`~]"""), "")
        .replace(Regex("""^\s*(?:root)?[\(\[\{]+"""), "")
        .replace(Regex("""[\)\]\}]+\s*$"""), "")
        .trim()

private fun mindmapTextLines(text: String): List<String> {
    val compact = text.replace(Regex("""\s+"""), " ").trim()
    if (compact.length <= 13) return listOf(compact)
    val firstBreak = compact
        .take(13)
        .indexOfLast { it == ' ' || it == '，' || it == '、' }
        .takeIf { it >= 5 }
        ?: 13
    val first = compact.take(firstBreak).trim()
    val remainder = compact.drop(firstBreak).trim()
    val second = if (remainder.length > 13) {
        remainder.take(12).trimEnd() + "…"
    } else {
        remainder
    }
    return listOf(first, second).filter(String::isNotBlank)
}

private enum class MindmapViewMode {
    DIAGRAM,
    MARKDOWN,
}

@Composable
internal fun MindmapViewer(
    markdown: String,
    title: String,
    modifier: Modifier = Modifier,
) {
    var mode by rememberSaveable(markdown) { mutableStateOf(MindmapViewMode.DIAGRAM) }
    val nodes = remember(markdown, title) { parseMindmapMarkdown(markdown, title) }
    val layout = remember(nodes) { layoutMindmap(nodes) }
    var fitRequest by remember { mutableIntStateOf(0) }

    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(
                selected = mode == MindmapViewMode.DIAGRAM,
                onClick = { mode = MindmapViewMode.DIAGRAM },
                label = { Text("导图") },
                leadingIcon = {
                    Icon(Icons.Outlined.AccountTree, contentDescription = null)
                },
            )
            FilterChip(
                selected = mode == MindmapViewMode.MARKDOWN,
                onClick = { mode = MindmapViewMode.MARKDOWN },
                label = { Text("Markdown") },
                leadingIcon = {
                    Icon(Icons.Outlined.Notes, contentDescription = null)
                },
            )
            if (mode == MindmapViewMode.DIAGRAM) {
                OutlinedButton(onClick = { fitRequest += 1 }) {
                    Icon(Icons.Outlined.FitScreen, contentDescription = null)
                    Text("适配")
                }
            }
        }

        if (mode == MindmapViewMode.MARKDOWN) {
            Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.surfaceContainer,
                shape = RoundedCornerShape(16.dp),
            ) {
                SelectionContainer {
                    Text(
                        text = markdown,
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState())
                            .padding(16.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        } else {
            MindmapCanvas(
                layout = layout,
                fitRequest = fitRequest,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Composable
private fun MindmapCanvas(
    layout: MindmapLayoutResult,
    fitRequest: Int,
    modifier: Modifier,
) {
    val density = LocalDensity.current.density
    var viewport by remember { mutableStateOf(IntSize.Zero) }
    var viewScale by remember(layout) { mutableStateOf(1f) }
    var viewOffset by remember(layout) { mutableStateOf(Offset.Zero) }
    val lineColor = MaterialTheme.colorScheme.outlineVariant
    val rootColor = MaterialTheme.colorScheme.primary
    val rootTextColor = MaterialTheme.colorScheme.onPrimary
    val firstLevelColor = MaterialTheme.colorScheme.secondaryContainer
    val firstLevelTextColor = MaterialTheme.colorScheme.onSecondaryContainer
    val nodeColor = MaterialTheme.colorScheme.surfaceContainerHigh
    val nodeTextColor = MaterialTheme.colorScheme.onSurface
    val backgroundColor = MaterialTheme.colorScheme.surfaceContainerLowest

    LaunchedEffect(layout, viewport, density, fitRequest) {
        if (viewport.width <= 0 || viewport.height <= 0 || layout.width <= 0f) {
            return@LaunchedEffect
        }
        val contentWidth = layout.width * density
        val contentHeight = layout.height * density
        viewScale = min(
            viewport.width / contentWidth,
            viewport.height / contentHeight,
        ).times(0.92f).coerceIn(0.15f, 2.5f)
        viewOffset = Offset(
            x = (viewport.width - contentWidth * viewScale) / 2f,
            y = (viewport.height - contentHeight * viewScale) / 2f,
        )
    }

    Box(
        modifier = modifier
            .background(backgroundColor, RoundedCornerShape(16.dp)),
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .onSizeChanged { viewport = it }
                .pointerInput(layout) {
                    detectTransformGestures { centroid, pan, zoom, _ ->
                        val oldScale = viewScale
                        val newScale = (oldScale * zoom).coerceIn(0.12f, 4f)
                        val contentPoint = Offset(
                            x = (centroid.x - viewOffset.x) / oldScale,
                            y = (centroid.y - viewOffset.y) / oldScale,
                        )
                        viewScale = newScale
                        viewOffset = Offset(
                            x = centroid.x - contentPoint.x * newScale + pan.x,
                            y = centroid.y - contentPoint.y * newScale + pan.y,
                        )
                    }
                },
        ) {
            val positionedById = layout.nodes.associateBy { it.node.id }
            withTransform({
                translate(viewOffset.x, viewOffset.y)
                scale(viewScale, viewScale, pivot = Offset.Zero)
            }) {
                layout.nodes.forEach { positioned ->
                    val parent = positioned.node.parentId?.let(positionedById::get)
                    if (parent != null) {
                        val start = Offset(
                            x = (parent.x + MINDMAP_NODE_WIDTH) * density,
                            y = (parent.y + MINDMAP_NODE_HEIGHT / 2f) * density,
                        )
                        val end = Offset(
                            x = positioned.x * density,
                            y = (positioned.y + MINDMAP_NODE_HEIGHT / 2f) * density,
                        )
                        val middleX = (start.x + end.x) / 2f
                        val path = Path().apply {
                            moveTo(start.x, start.y)
                            cubicTo(middleX, start.y, middleX, end.y, end.x, end.y)
                        }
                        drawPath(
                            path = path,
                            color = lineColor,
                            style = androidx.compose.ui.graphics.drawscope.Stroke(
                                width = 2f * density,
                            ),
                        )
                    }
                }

                layout.nodes.forEach { positioned ->
                    val isRoot = positioned.node.depth == 0
                    val isFirstLevel = positioned.node.depth == 1
                    val background = when {
                        isRoot -> rootColor
                        isFirstLevel -> firstLevelColor
                        else -> nodeColor
                    }
                    val foreground = when {
                        isRoot -> rootTextColor
                        isFirstLevel -> firstLevelTextColor
                        else -> nodeTextColor
                    }
                    val left = positioned.x * density
                    val top = positioned.y * density
                    drawRoundRect(
                        color = background,
                        topLeft = Offset(left, top),
                        size = Size(
                            width = MINDMAP_NODE_WIDTH * density,
                            height = MINDMAP_NODE_HEIGHT * density,
                        ),
                        cornerRadius = CornerRadius(14f * density),
                    )
                    val lines = mindmapTextLines(positioned.node.text)
                    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                        color = foreground.toArgb()
                        textSize = 14f * density
                        typeface = if (isRoot) {
                            android.graphics.Typeface.DEFAULT_BOLD
                        } else {
                            android.graphics.Typeface.DEFAULT
                        }
                    }
                    val lineHeight = 18f * density
                    val blockHeight = lines.size * lineHeight
                    val firstBaseline = top +
                        (MINDMAP_NODE_HEIGHT * density - blockHeight) / 2f +
                        14f * density
                    lines.forEachIndexed { index, text ->
                        drawContext.canvas.nativeCanvas.drawText(
                            text,
                            left + 12f * density,
                            firstBaseline + index * lineHeight,
                            paint,
                        )
                    }
                }
            }
        }
    }
}
