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

## Android 行为

- 服务器地址保存在 Capacitor Preferences。
- API Token 使用 Android Keystore AES-GCM 加密，密文只保存在 App 私有目录。
- API 请求携带 Bearer Token；受保护的媒体请求使用连接验证时建立的 HttpOnly 会话。
- Android 分享面板的文本和 URL 会进入“处理”页。
- 摘要、字幕和导图文本下载到系统 `Downloads/MPP` 目录。
- 原生 SQLite 保存归档索引、revision、文件清单和同步 cursor；正文和图片保存到 App 私有目录。
- 首次连接、App 启动、回到前台和手动操作会执行增量同步；同步并发下载 3 个文件并逐个校验 SHA-256。
- 离线状态可继续使用文件列表、搜索、筛选、摘要、字幕、导图、封面和图文图片；音视频保持在线播放。
- 设置页提供立即同步、重建本地索引和清空离线资料。
- 主题支持跟随系统、浅色和深色；状态栏、导航栏与 Web UI 同步。
- Android 端保留 URL 提交、任务控制、归档阅读和媒体播放，界面隐藏本地路径、目录浏览、上传和归档修改。

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
