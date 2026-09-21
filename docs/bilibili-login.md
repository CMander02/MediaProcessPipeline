# B 站扫码登录与下载回退

在设置的「哔哩哔哩」卡片点击「扫码登录」，使用哔哩哔哩 App 扫码并确认。服务端验证登录后将凭据保存到现有 runtime settings；页面只接收登录状态，后续下载和字幕请求自动使用保存的凭据。二维码过期后重新获取，取消或离开页面会停止轮询。手动 Cookie 配置保留在折叠项内。

登录失效时，下载端使用不带 Cookie 的普通播放接口申请 360P 视频。已登录下载遇到播放接口或媒体传输失败时，也会重新申请匿名 360P 地址。连续媒体文件通过 ffmpeg 封装，原有 DASH 视频与音频合并流程继续使用。

每个 CDN 地址先使用 urllib；网络传输失败时改用 httpx，随后尝试备用地址。两种客户端都保留应用的代理配置和 TLS 证书验证。SSL EOF 表示 TLS 连接提前关闭，具体网络原因需要结合代理和 CDN 连通性判断。

更新后重启后端并刷新页面，前端产物由 `npm run build` 生成。

## 参考实现

本机桌面版：`C:/Program Files/DownKyi-1.0.23-1.win-x64`，程序集版本 1.0.23；程序内嵌的项目链接为 [yaobiao131/downkyicore](https://github.com/yaobiao131/downkyicore)。该仓库目前声明停止维护。保留代码的分支提供了以下协议参考：

- [二维码申请与轮询](https://github.com/Mu-L/downkyicore/blob/main/DownKyi.Core/BiliApi/Login/LoginQR.cs)
- [扫码状态处理](https://github.com/Mu-L/downkyicore/blob/main/DownKyi/ViewModels/ViewLoginViewModel.cs)
- [登录凭据保存](https://github.com/Mu-L/downkyicore/blob/main/DownKyi.Core/BiliApi/Login/LoginHelper.cs)

项目内的 Python 服务和 React 组件按上述接口流程实现，使用现有配置存储、网络设置和页面生命周期。
