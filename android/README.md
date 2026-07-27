# MPP Remote for Android

MPP Remote 是 MediaProcessPipeline 的 Android 远程投递客户端。它使用 Google 官方
Android 技术栈：Kotlin、Jetpack Compose、Android Gradle Plugin 和 Android Studio。

当前 MVP 支持：

- 同步并搜索远程 MPP 内容库；
- 查看处理结果的摘要、网页原文、转录、思维导图和详情；
- 浏览远程服务器最近的任务及处理状态；
- 在“跟随系统、浅色、深色”之间即时切换并保存外观偏好；
- 使用 Google Material Icons，与桌面端、网页端保持一致的导航语义；
- 作为 `text/*` 分享目标出现在 Android 系统分享面板；
- 保存远程 MPP Server URL；
- 使用 Android Keystore 加密保存 API Token；
- 调用 `POST /api/tasks` 提交链接或整段分享文案；
- Debug 构建允许 HTTP，Release 构建要求 HTTPS。

App 首页是内容库，服务器设置位于底部“设置”标签。网页端处理完成的归档会通过同一套
MPP API 自动同步到手机，无需额外复制文件；手机阅读时按需加载封面和文档内容。

## Windows 开发环境

只需安装 [Android Studio](https://developer.android.com/studio)。Android Studio 已包含
适用的 JDK 和 Kotlin 支持，Kotlin 编译器由 Gradle 自动管理。

首次打开 Android Studio 后，通过 **Tools → SDK Manager** 安装：

- Android SDK Platform 36.1；
- Android SDK Build-Tools 36.0.0；
- Android SDK Platform-Tools。

Google Android Emulator 是可选组件。使用 MuMu 模拟器或 USB 真机时可以跳过它；
截图中的 Emulator 下载失败不会影响当前项目编译。这个 Kotlin/Compose 客户端也不需要
Android NDK。

在 Android Studio 中选择 **Open**，打开仓库里的 `android/` 目录，等待 Gradle Sync
完成，然后选择模拟器并点击 **Run**。

## 连接 Windows 上的 MPP

### Android Emulator

启动允许外部访问的后端：

```powershell
cd backend
uv run python -m app.cli serve --host 0.0.0.0 --port 18000
```

App 的 Server URL 填写：

```text
http://10.0.2.2:18000
```

`10.0.2.2` 是 Android Emulator 访问 Windows 主机的专用地址。首次监听局域网地址时，
Windows 防火墙可能询问是否允许访问，开发网络选择“专用网络”即可。

### MuMu 模拟器

MuMu 12 开启 ADB 后会作为普通 Android 设备出现在 `adb devices` 中。当前测试实例的
设备序列号为 `emulator-5554`：

```powershell
adb -s emulator-5554 install -r -t .\app\build\outputs\apk\debug\app-debug.apk
adb -s emulator-5554 reverse tcp:18000 tcp:18000
adb -s emulator-5554 shell am start -n com.mpp.remote/.MainActivity
```

App 的 Server URL 填写 `http://localhost:18000`。`adb reverse` 会把模拟器的
`localhost:18000` 转发到 Windows 上的 MPP。

### USB 真机

在手机中开启 **开发者选项 → USB 调试**，连接 Windows 后执行：

```powershell
adb devices
adb reverse tcp:18000 tcp:18000
```

保持 MPP 使用默认的 `localhost:18000` 运行，App 的 Server URL 填写：

```text
http://localhost:18000
```

也可以让手机和电脑连接同一 Wi-Fi，后端使用 `--host 0.0.0.0`，App 填写电脑局域网
地址，例如 `http://192.168.1.20:18000`。

### REDMI K90 Pro Max

REDMI K90 Pro Max 出厂系统为基于 Android 16 的小米澎湃 OS 3，CPU 使用 64 位 ARM
架构。当前 App 的 `minSdk` 为 26、`targetSdk` 为 36，APK 包含 `arm64-v8a`，可直接
安装运行。手机更新系统后，可在 **设置 → 我的设备 → 全部参数与信息** 查看实际 Android
版本。

安装调试版 APK 可选择：

- USB 调试连接 Windows 后运行
  `adb -d install -r -t .\app\build\outputs\apk\debug\app-debug.apk`；
- 将 APK 复制到手机的“下载”目录，在文件管理中打开，并按系统提示允许该文件管理器
  “安装未知应用”。

USB 调试时可继续使用 `adb reverse` 和 `http://localhost:18000`。同一 Wi-Fi 调试时，
填写 Windows 的局域网地址。日常远程使用时填写可公网访问的 HTTPS 域名。

## 命令行调试

从仓库根目录运行：

```powershell
.\scripts\android.ps1 doctor
.\scripts\android.ps1 test
.\scripts\android.ps1 build
.\scripts\android.ps1 install
.\scripts\android.ps1 run
.\scripts\android.ps1 logcat
```

也可以直接进入 Android 项目：

```powershell
cd android
.\gradlew.bat test
.\gradlew.bat assembleDebug
.\gradlew.bat installDebug
```

Debug APK 输出到：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Release AAB 使用：

```powershell
cd android
.\gradlew.bat bundleRelease
```

发布前需要配置 Android 签名密钥。

## 测试系统分享入口

安装并启动 App 后，可用 ADB 直接模拟其他 App 分享链接：

```powershell
adb shell am start `
  -n com.mpp.remote/.MainActivity `
  -a android.intent.action.SEND `
  -t text/plain `
  --es android.intent.extra.TEXT "https://www.bilibili.com/video/BV1xx411c7mD"
```

Android Studio 支持 Kotlin 断点、变量查看、Compose Layout Inspector、Network Inspector
和 Logcat。`MainActivity.consumeShareIntent()` 适合设置第一个分享入口断点，
`MppApiClient.request()` 适合观察 HTTP 请求。

## 服务端要求

远程服务需要设置 `api_token`。App 发出的写请求包含：

- `Authorization: Bearer <token>`
- `X-Requested-With: mpp-android`

公网环境使用 HTTPS 地址。Debug APK 中的 HTTP 支持用于模拟器、USB 端口反向代理和
受信任的局域网开发。
