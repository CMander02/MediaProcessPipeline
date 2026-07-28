package com.mpp.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MindmapViewerTest {
    @Test
    fun parsesNestedMarkdownIntoParentChildTree() {
        val nodes = parseMindmapMarkdown(
            """
            # 总主题
            - 分支 A
              - 子节点 A1
            - 分支 B
            """.trimIndent(),
        )

        assertEquals("总主题", nodes[0].text)
        assertEquals(nodes[0].id, nodes[1].parentId)
        assertEquals(nodes[1].id, nodes[2].parentId)
        assertEquals(nodes[0].id, nodes[3].parentId)
    }

    @Test
    fun suppliesRootForBulletOnlyMarkdown() {
        val nodes = parseMindmapMarkdown("- 第一项\n- 第二项", "内容标题")

        assertEquals("内容标题", nodes.first().text)
        assertEquals(3, nodes.size)
        assertTrue(nodes.drop(1).all { it.parentId == nodes.first().id })
    }

    @Test
    fun layoutExpandsAcrossDepthAndLeaves() {
        val nodes = parseMindmapMarkdown(
            "# 根\n- 一\n  - 一点一\n- 二",
        )
        val layout = layoutMindmap(nodes)

        assertEquals(nodes.size, layout.nodes.size)
        assertTrue(layout.width > 500f)
        assertTrue(layout.height > 100f)
    }
}
