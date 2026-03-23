# 多类型文件支持扩展方案

日期: 2026-03-21

## 背景

当前 pipeline 围绕"音视频→转录→LLM 分析"设计。需要扩展支持论文、图片、PDF、DJVU、EPUB 等多种文件类型。

## 内容抽取层

核心思路：所有文件类型统一到 `ExtractedContent(text, metadata, segments?)` 中间表示。

### 按类型分发

| 类型 | 抽取方式 | 备注 |
|------|----------|------|
| 论文 PDF | PyMuPDF/pdfplumber 提文本+表格，Nougat/Marker 做 LaTeX-aware OCR | 双栏、公式是难点 |
| 扫描 PDF/DJVU | OCR pipeline (Surya/PaddleOCR) | DJVU 先转图再 OCR |
| EPUB | 直接解析 HTML/XML，已是结构化文本 | 最简单 |
| 图片 | VLM 描述 (Qwen-VL) 或 OCR | 看是照片还是文档扫描 |
| 音视频 | 现有 pipeline 不变 | 已实现 |

### 接口设计

```python
class ContentExtractor(Protocol):
    def extract(self, file_path: Path) -> ExtractedContent: ...

@dataclass
class ExtractedContent:
    text: str
    metadata: dict[str, Any]
    segments: list[TextSegment] | None = None  # 带位置信息的分段
    source_type: str = ""
```

按 MIME type 分发到不同 extractor，下游 LLM 分析/摘要/归档复用。

## 存储方案

### 短期（够用）

- 保持现有 `data/{task_id_short}_{title}/` 目录结构
- SQLite tasks 表加列: `content_type`, 区分来源类型
- 原始文件放 `source/`，抽取结果放同目录
- 不需要改动归档结构

### 中期（量上来后）

- 原始文件 content-addressed 存储（按 hash 去重，论文经常重复下载）
- 全文检索: SQLite FTS5（单用户工具完全够）
- 向量检索（语义搜索）: SQLite + sqlite-vec 或 ChromaDB
- 不需要 PostgreSQL / Mongo / S3，保持简单

## 架构改动路径

最小改动：

1. `TaskType` 枚举加 `DOCUMENT`, `IMAGE` 等
2. `run_pipeline` 根据 type 走不同的抽取分支，后续 LLM 分析复用
3. 归档结构不变，`metadata.json` 加 `source_type` 字段

本质：pipeline 第一段（download→separate→transcribe）替换为按类型分发的 extractor，分析和归档逻辑共享。

## 优先级建议

1. PDF 文本提取（覆盖论文和一般文档）
2. EPUB 解析（最简单，投入产出比高）
3. 图片 OCR/VLM（复用现有 Qwen 基础设施）
4. DJVU（小众，按需）
