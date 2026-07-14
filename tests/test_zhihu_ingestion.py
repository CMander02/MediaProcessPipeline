import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion.platform.zhihu import api as zhihu_api  # noqa: E402


def test_zhihu_html_to_markdown_preserves_structure():
    markdown = zhihu_api._html_to_markdown(
        """
        <h2>小标题</h2>
        <p>第一段<strong>重点</strong>，参见<a href="https://example.com/a">链接</a>。</p>
        <p>第二段</p>
        <p>行一<br/>行二<br/>行三</p>
        <blockquote><p>引用内容</p></blockquote>
        <ol><li>第一项</li><li>第二项</li></ol>
        <figure>
          <img data-original="https://picx.zhimg.com/v2-demo.jpg" alt="结构图">
          <figcaption>图注文字</figcaption>
        </figure>
        <pre><code>print(1)</code></pre>
        """
    )

    assert "## 小标题" in markdown
    assert "**重点**" in markdown
    assert "[链接](https://example.com/a)" in markdown
    assert "链接](https://example.com/a)。\n\n第二段" in markdown
    assert "行一\n行二\n行三" in markdown
    assert "> 引用内容" in markdown
    assert "1. 第一项" in markdown
    assert "2. 第二项" in markdown
    assert "![结构图](https://picx.zhimg.com/v2-demo.jpg)" in markdown
    assert "_图注文字_" in markdown
    assert "```\nprint(1)\n```" in markdown


def test_extract_answer_uses_markdown_description_and_ordered_images():
    url = "https://www.zhihu.com/question/123/answer/456"
    first_image = "https://picx.zhimg.com/v2-first.jpg"
    second_image = "https://pic1.zhimg.com/v2-second.png"
    state = {
        "entities": {
            "questions": {
                "123": {
                    "title": "如何保留知乎结构？",
                    "detail": "<p>问题背景<strong>很重要</strong></p>",
                }
            },
            "answers": {
                "456": {
                    "content": (
                        "<h2>结论</h2>"
                        "<p>第一段<a href=\"https://example.com\">参考</a></p>"
                        "<ul><li>要点 A</li><li>要点 B</li></ul>"
                        f"<img src=\"{first_image}\" alt=\"第一张\">"
                        "<blockquote><p>保留引用</p></blockquote>"
                        f"<figure><img data-original=\"{second_image}\" alt=\"第二张\"></figure>"
                    ),
                    "author": {"name": "作者"},
                    "createdTime": 1780000000,
                    "voteupCount": 12,
                    "commentCount": 3,
                }
            },
        }
    }

    info = zhihu_api._extract_answer(url, state)

    assert info["description"].startswith("# 如何保留知乎结构？")
    assert "问题背景**很重要**" in info["description"]
    assert "## 结论" in info["description"]
    assert "[参考](https://example.com)" in info["description"]
    assert "- 要点 A" in info["description"]
    assert "> 保留引用" in info["description"]
    assert f"![第一张]({first_image})" in info["description"]
    assert info["content_subtype"] == "image_note"
    assert info["thumbnail"] == first_image
    assert info["extra"]["image_urls"] == [first_image, second_image]
    assert info["extra"]["content_format"] == "markdown"


def test_extract_pin_uses_markdown_description():
    url = "https://www.zhihu.com/pin/789"
    image = "https://picx.zhimg.com/v2-pin.webp"
    state = {
        "entities": {
            "pins": {
                "789": {
                    "contentHtml": (
                        "<p>想法第一段</p>"
                        "<blockquote><p>引用一句</p></blockquote>"
                        "<ol><li>步骤一</li><li>步骤二</li></ol>"
                        f"<img src=\"{image}\" alt=\"配图\">"
                    ),
                    "excerptTitle": "<p>想法标题</p>",
                    "author": {"name": "作者"},
                    "created": 1780000000,
                    "likeCount": 4,
                    "commentCount": 2,
                }
            }
        }
    }

    info = zhihu_api._extract_pin(url, state)

    assert info["title"] == "想法标题"
    assert "想法第一段" in info["description"]
    assert "> 引用一句" in info["description"]
    assert "1. 步骤一" in info["description"]
    assert f"![配图]({image})" in info["description"]
    assert info["content_subtype"] == "image_note"
    assert info["extra"]["image_urls"] == [image]
