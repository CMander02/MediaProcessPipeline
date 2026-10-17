from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis.source_context import _timeline  # noqa: E402, I001


XU_TIANYIN_TIMELINE = """\
00:00:00 片头观点：取消最佳论文奖、AI 奖励投机与系统漏洞
00:00:34 嘉宾介绍：UIUC 教授徐天音，访问 UC Berkeley SkyLab
00:00:59 第一次申请美国 PhD：被 24 所学校拒绝
00:03:52 Gap Year：前往德国哥廷根大学交流
00:04:22 足球、竞技体育与系统研究的共同点
00:05:47 第二次申请：靠坚持拿到 UCSD Offer
00:08:37 加入 YY 实验室：导师如何系统化训练研究思维
00:10:26 PCheck：从配置错误到容错系统
00:13:23 为什么 Best Paper 具有随机性和主观性
00:15:49 《Do Not Blame Users for Misconfigurations》：不要把错误都归咎于用户
00:19:38 AI Agent 成为系统用户后，旧系统为什么会失效
00:21:58 为 AI Agent 重新设计登录、安全与操作接口
00:24:12 Rock-star Advisor：好导师如何影响研究热情与创业认知
00:27:00 为什么选择 UIUC 和香槟这座“玉米地”
00:28:29 如何选择学生：导师与学生之间的信任关系
00:32:56 人人都可以做 Research：研究是一种反复探索
00:36:09 PhD 前两年怎么训练：少从论文想 Idea，多从真实问题出发
00:38:37 AI Agent 能否取代导师和高年级学生
00:41:34 AI 时代，传统论文和同行评审要不要改革
00:44:08 AI 放大研究生产力，也可能进一步拉大能力差距
00:45:44 如何持续做出好研究：招对学生并理解他的目标
00:49:48 Fight Dragons, Not Windmills：解决真正重要的问题
00:50:28 如何从小问题挖到基础而深刻的研究问题
00:53:09 如何教操作系统：用 Linux 内核训练复杂工程能力
00:57:31 AI 时代计算机科学会不会消亡
00:58:38 AI SRE 能做什么，人类又该扮演什么角色
01:03:11 SREGym：在真实故障环境中评估 AI 运维能力
01:04:44 Benchmark 如何随着模型能力持续升级
01:09:14 AI 加形式化方法：把代码转化为形式化模型
01:09:30 Model Checking 入门：Safety、Liveness 与系统抽象
01:15:22 从五年到五小时：AI 如何加速形式化建模
01:16:29 形式化验证对企业的价值：从事后调试到事前证明
01:18:27 形式化证明的成本，以及为什么先选择 Model Checking
01:19:31 SDD：用规格驱动开发约束 AI 生成代码
01:21:28 形式化验证的鸿沟：最难的是定义正确的需求和规格
01:23:05 理想的 Agent-native 系统：容忍错误、提供反馈、持续恢复
01:24:21 ROC：面向恢复的计算与“系统一定会出错”的现实
01:26:15 人为操作为什么是系统故障的主要来源
01:29:13 Undo 与 Microreboot：把经典系统思想带入 Agent 时代
01:31:07 Reliability 与 Security 为什么迎来新的研究机会
01:33:00 学术休假：为什么到 UC Berkeley SkyLab 访问
01:35:26 UIUC 与 Berkeley：不同研究文化如何互相启发
01:38:22 工业界研究与学术研究的目标差异
01:40:23 PhD 实习：深入生产系统，而不是重复学校里的研究
01:42:44 去 Google 做核心系统，还是到大学当教授
01:45:52 如何判断一份工业界工作和一个团队是否值得加入
01:47:21 AI 时代最重要的能力：持续学习
01:49:05 给年轻研究者的三个建议：问题、方法与技能
01:53:32 想创业或进工业界，PhD 阶段应该怎样准备
01:56:17 结语：让 Research 成为每天的实践
"""


def test_description_timeline_extracts_xu_tianyin_sample_without_external_files():
    timeline = _timeline(
        {
            "title": "徐天音：系统与研究",
            "description": XU_TIANYIN_TIMELINE,
            "duration_seconds": 7052,
            "chapters": [],
        }
    )

    assert len(timeline) == 50
    assert timeline[0].start == 0
    assert timeline[0].title.startswith("片头观点")
    assert timeline[-1].start == 6977
    assert all(item.source == "source_description" for item in timeline)
    assert all(item.evidence == "metadata.description" for item in timeline)


def test_existing_chapters_take_priority_over_description_timeline():
    timeline = _timeline(
        {
            "description": "00:00:00 描述中的章节\n00:01:00 描述中的第二章",
            "chapters": [{"title": "来源章节", "start_time": 120}],
            "duration_seconds": 300,
        }
    )

    assert [item.title for item in timeline] == ["开场与嘉宾介绍", "来源章节"]
    assert timeline[-1].source == "source_chapter"
    assert all("描述中的" not in item.title for item in timeline)


def test_description_timeline_requires_standalone_line_and_valid_duration():
    timeline = _timeline(
        {
            "description": (
                "会议在 09:30 开始，普通 prose 不应成为章节。\n"
                "00:00:00 有效开场\n"
                "00:03:00 - 有效结尾\n"
                "99:99 无效秒数\n"
                "99:59:00 超出视频时长\n"
                "[00:04:00] 方括号格式也支持\n"
            ),
            "chapters": [],
            "duration_seconds": 240,
        }
    )

    assert [(item.start, item.title) for item in timeline] == [
        (0, "有效开场"),
        (180, "有效结尾"),
        (240, "方括号格式也支持"),
    ]
