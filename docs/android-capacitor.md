# Capacitor Android 构建与调试

MPP 0.5.0 使用 `web/src/` 作为 PC、服务器和 Android 的唯一 UI 源码。`web/android/` 承载 Android 启动、Keystore、安全分享、文本下载、离线资料库、系统返回键和构建配置。

## 环境

- Node.js 22 或更高版本
- Android Studio 2025.2.1 或更高版本，使用内置 JBR 21
- Android SDK Platform 36、Build Tools 35+
- 已连接的 Android 设备或模拟器

## 常用命令

在 `web/` 目录运行：

```powershell
npm run android:add    # 仅在 Android 工程尚未生成时使用
npm run android:sync   # 构建 web/dist 并同步原生工程
npm run android:run    # 同步、安装并启动 Debug App
npm run android:debug  # 生成可侧载 Debug APK
npm run android:apk    # 生成 Release APK
npm run android:aab    # 生成应用商店 Release AAB
npm run android:check  # 前端与 Android 完整检查
npm run android:open   # 使用 Android Studio 打开工程
```

Debug APK 位于 `web/android/app/build/outputs/apk/debug/app-debug.apk`，Release APK/AAB 位于 `web/android/app/build/outputs/apk/release/` 和 `web/android/app/build/outputs/bundle/release/`。`versionName` 直接读取仓库根目录 `pyproject.toml`。

## 连接本机服务

FastAPI 固定使用 18000 端口。本机服务启动后，为 USB 设备或模拟器建立反向端口：

```powershell
adb reverse tcp:18000 tcp:18000
```

App 的服务器地址填写 `http://localhost:18000`。局域网设备可填写 `http://192.168.x.x:18000`；长期远程访问使用 HTTPS 域名。

Debug 构建允许 localhost 与局域网 HTTP，Release 构建要求 HTTPS。服务器需要在防火墙和反向代理中开放对应入口。

构建时可通过 `VITE_MPP_DEFAULT_SERVER_URL` 预填首次启动的服务器地址。用户保存过的地址优先；API Token 继续由用户输入并写入 Android Keystore。

## Android 行为

- 服务器地址保存在 Capacitor Preferences。
- API Token 使用 Android Keystore AES-GCM 加密，密文只保存在 App 私有目录。
- API 请求携带 Bearer Token；受保护的媒体请求使用连接验证时建立的 HttpOnly 会话。
- Android 分享面板的文本和 URL 会进入“处理”页。
- 摘要、字幕和导图文本下载到系统 `Downloads/MPP` 目录。
- 原生 SQLite 保存归档索引、revision、文件清单和同步 cursor；正文和图片保存到 App 私有目录。
- 联网时文件页直接读取服务器目录，包括新建文件夹、排队中、处理失败和已完成的内容；服务器不可达时回退到下载过的离线归档。
- 前台通过共享 SSE 接收任务和文件夹变更通知，合并 300ms 内的事件后刷新。每 15 秒补查一次；回到前台、网络恢复和 SSE 重连时也刷新。进入后台后暂停定时同步。
- 首次连接、App 启动、回到前台和目录刷新会执行增量同步；同步并发下载 3 个文件并逐个校验 SHA-256。
- 离线状态可继续使用文件列表、搜索、筛选、摘要、字幕、导图、封面和图文图片；音视频保持在线播放。
- 设置页提供立即同步、重建本地索引和清空离线资料。
- 主题支持跟随系统、浅色和深色；状态栏、导航栏与 Web UI 同步。
- Android 端保留 URL 提交、任务控制、归档阅读和媒体播放，界面隐藏本地路径、目录浏览、上传和归档修改。

## 离线资料库隔离

服务器地址和服务端 `workspace_id` 共同确定离线资料库。不同组合使用独立 SQLite 数据库、文件目录和同步 cursor。切换服务器或资料库会停止旧同步。切回已连接的组合会恢复该组合的离线资料。

同步先读取 `/api/sync/status`。索引、清单和文件请求携带 `X-MPP-Workspace-ID`（URI 编码）。服务器切库后会拒绝旧标识。旧服务器缺少该接口时，不会继续向未知资料库同步。离线启动只读取该服务器最近确认的资料库。

新布局位于 App 私有目录：`databases/offline_archives_<UUID>.db` 和 `files/offline_workspaces/<UUID>/`。`shared_prefs/offline_workspaces.xml` 保存服务器、资料库与 UUID 的对应关系。清空离线资料只清空当前组合。

升级前的 `databases/offline_archives.db`、`files/offline_archives/` 和 `files/offline_tmp/` 原样保留。旧缓存没有来源标识，不能安全地自动归入当前资料库，因此新版本不会显示它。连接原服务器后重新同步即可恢复阅读。若原服务器已不可用，恢复时需保留这三个旧路径并先核对来源；不要卸载 App 或清除 App 数据。

`node scripts/android-gradle.mjs testDebugUnitTest` 使用 Robolectric 运行真实 SQLite、文件目录和本机 HTTP 同步回归，覆盖切服、同服切库、切回恢复、迟到响应和旧缓存保留。它不代替真机网络与生命周期检查。

## Release 签名

Release 构建链从环境变量读取签名配置：

```powershell
$env:MPP_ANDROID_KEYSTORE = "C:\secure\mpp-release.jks"
$env:MPP_ANDROID_STORE_PASSWORD = "..."
$env:MPP_ANDROID_KEY_ALIAS = "mpp"
$env:MPP_ANDROID_KEY_PASSWORD = "..."
npm run android:apk
npm run android:aab
```

Keystore、密码和 APK/AAB 均保持在仓库外。Release 编译检查可独立运行；正式分发产物使用固定的长期签名密钥和上述环境变量构建。

## GitHub 分发与手机更新

建议使用 GitHub Releases 发布 APK，再用 [Obtainium](https://github.com/ImranR98/Obtainium) 在手机检查更新和接收新版本提醒。GitHub 保存安装包；Obtainium 从 Release 下载并调用 Android 安装流程。安装是否需要点确认取决于 Android 版本和安装权限。

`.github/workflows/android-release.yml` 提供两个入口：

- Actions → Android APK → Run workflow：检查前端、运行原生测试与 Lint、构建签名 APK，保存为工作流产物。
- 发布 `vX.Y.Z` Release：构建同一标签的代码，检查标签与 `pyproject.toml` 一致，然后将 `MPP-android.apk` 上传到该 Release。上传失败时查看 Actions 日志；构建完成前 Release 中还没有 APK。

首次需要在仓库 Settings → Secrets and variables → Actions 配置以下 Secrets：

| Secret | 内容 |
| --- | --- |
| `MPP_ANDROID_KEYSTORE_BASE64` | 长期签名 keystore 文件的 Base64 |
| `MPP_ANDROID_STORE_PASSWORD` | keystore 密码 |
| `MPP_ANDROID_KEY_ALIAS` | 签名别名 |
| `MPP_ANDROID_KEY_PASSWORD` | 签名密钥密码 |

密钥应单独备份。不要将密钥或密码提交到仓库。不要在每次构建时生成新密钥。

每次正式更新先增加 `pyproject.toml` 的版本号，再创建同名标签和 Release。当前 Android `versionCode` 按 `major * 10000 + minor * 100 + patch` 生成，minor 和 patch 应小于 100，新的 versionCode 必须递增。包名保持 `com.mpp.remote`。覆盖安装需要相同签名；现有 Debug APK 与新的正式签名 APK 通常不能互相覆盖。首次切换前先核对已安装 APK 的签名，保留需要的离线资料。

手机安装 Obtainium 后添加仓库 `https://github.com/CMander02/MediaProcessPipeline`，APK 筛选设为 `^MPP-android\.apk$`，按需要启用后台检查和更新通知。正式用户默认排除预发布版本。

云端内容同步不依赖 APK 更新。发布 APK 也不会更新云端服务：同步修复需将后端与 `web/dist` 一并部署到云端，安卓则安装包含新前端的 APK。
