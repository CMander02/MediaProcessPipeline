# yt-dlp 启动检查与更新

yt-dlp 的站点解析器需要跟随视频网站变化更新。启动自动更新只针对这个依赖，由 `ytdlp_auto_update` 控制。新配置默认开启，已保存的 `false` 继续有效。

每次启动检查兼容当前 Python 的稳定版本。检查和安装的源顺序为：

1. 清华：`https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`
2. 中科大：`https://mirrors.ustc.edu.cn/pypi/simple`
3. 系统默认源：继承 uv 的项目、用户配置及环境变量；没有 uv 时使用当前 Python 的 pip 配置。

每个源单独尝试，失败后进入下一个源。第一个成功返回兼容版本的源决定检查结果。镜像版本较旧时保留本地版本，后续启动继续检查。版本按 PEP 440 比较，`2026.03.17` 和 `2026.3.17` 视为同一版本。检查超时上限为每源 20 秒，安装超时上限为每源 180 秒。全部检查失败会报告“版本检查失败”，保留当前安装。

安装使用 uv sync 的 `--upgrade-package` 并更新本地 `uv.lock`，继续保留项目 PyTorch 的专用索引配置。安装版本下限取已安装版本和本次检查版本中的较高者。只有找到新版本或依赖缺失时才在启动期间安装；检查不会修改 Python 环境。

设置页的“服务 → yt-dlp”显示检查来源和失败状态，也支持手动更新。版本发生变化后，手动更新接口会安排后端重启；安装与已加载版本均保持不变时返回成功且无需重启。启动更新后，如果进程已经加载旧版 yt-dlp，日志会提示重启以使用新版。

相关入口：

- 状态：`GET /api/settings/ytdlp`
- 手动更新：`POST /api/settings/ytdlp/upgrade`
- CLI：`mpp upgrade-ytdlp`
- 配置：`mpp config ytdlp_auto_update true`

索引及解析行为参考 [uv package indexes](https://docs.astral.sh/uv/concepts/indexes/) 和 [uv CLI](https://docs.astral.sh/uv/reference/cli/)。
