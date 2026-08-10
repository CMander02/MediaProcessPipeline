import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion.platform.apple_podcast.api import (  # noqa: E402
    _html_to_markdown,
    _normalize_rss_item,
)


def test_rich_rss_description_preserves_structure_links_and_images():
    source = """
    <p><strong>活动预告</strong></p>
    <p><strong>【时光机】</strong></p>
    <p><strong>Part 1 后训练</strong></p>
    <ul><li><p><a class="timestamp">01:15</a> 创业感受</p></li></ul>
    <p>查看 <a href="https://example.com/report">RSIBench-Data</a></p>
    <img src="https://example.com/qr.png" alt="报名二维码">
    """

    markdown = _html_to_markdown(source)

    assert "**活动预告**" in markdown
    assert "## 时光机" in markdown
    assert "### Part 1 后训练" in markdown
    assert "- 01:15 创业感受" in markdown
    assert "[RSIBench-Data](https://example.com/report)" in markdown
    assert "![报名二维码](https://example.com/qr.png)" in markdown


def test_rss_content_encoded_is_kept_separately_from_description():
    item = ET.fromstring(
        """
        <item xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <title>示例</title>
          <description>纯文本</description>
          <content:encoded><![CDATA[<p><strong>富文本</strong></p>]]></content:encoded>
        </item>
        """
    )

    normalized = _normalize_rss_item(item)

    assert normalized["description"] == "纯文本"
    assert normalized["content_encoded"] == "<p><strong>富文本</strong></p>"
