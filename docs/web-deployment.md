# MPP Web/PWA 启动与部署

MPP 使用同一份 React/Vite 前端服务本机 PC、服务器浏览器和手机 PWA。FastAPI 在 `18000` 端口同时提供网页、API 与 SSE。

## 环境准备

- Python 3.11～3.12
- `uv`
- Node.js 与 npm
- FFmpeg
- 按实际推理方式准备 CUDA、模型与 API 密钥

首次安装：

```bash
uv sync
cd web
npm ci
npm run build
cd ..
```

启动脚本会检查 `web/dist`。源码或构建配置更新后，脚本会自动执行生产构建。

## Windows 本机

双击根目录的 `start.bat`，或在 PowerShell 中运行：

```powershell
.\scripts\start-web.ps1
```

健康检查通过后，浏览器会打开 `http://localhost:18000`。关闭窗口或按 `Ctrl+C` 会停止后端及其子进程。

可用参数：

```powershell
.\scripts\start-web.ps1 -NoBrowser
.\scripts\start-web.ps1 -Host localhost -Port 18000
.\scripts\start-web.ps1 -Server
```

## Linux 本机与服务器

本机启动：

```bash
./scripts/start-web.sh
```

服务器启动：

```bash
./scripts/start-web.sh --server --no-browser
```

`--server` 监听 `0.0.0.0:18000`。服务器模式会检查 API Token；Token 为空时直接退出。可以先通过本机网页的“设置 → 访问控制”配置，也可以在服务器上运行：

```bash
cd backend
uv run python -m app.cli config set api_token "替换为足够长的随机值"
```

通用参数：

```text
--server
--no-browser
--host ADDRESS
--port PORT
--health-timeout SECONDS
```

## systemd

仓库提供 [mpp-web.service](../deploy/systemd/mpp-web.service)。模板默认使用用户 `mpp` 和目录 `/opt/MediaProcessPipeline`，安装前按实际路径修改。

```bash
sudo cp deploy/systemd/mpp-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mpp-web
sudo systemctl status mpp-web
journalctl -u mpp-web -f
```

更新版本后在项目目录重新安装依赖并构建，再重启服务：

```bash
uv sync
cd web && npm ci && npm run build && cd ..
sudo systemctl restart mpp-web
```

## HTTPS 反向代理

远程 PWA 安装和会话 Cookie 推荐通过 HTTPS 域名访问。服务继续监听 `0.0.0.0:18000`，Caddy 或 Nginx 负责 TLS 与域名。

Caddy 示例：

```caddyfile
mpp.example.com {
    reverse_proxy localhost:18000
}
```

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name mpp.example.com;

    location / {
        proxy_pass http://localhost:18000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }
}
```

`proxy_buffering off` 让 SSE 任务进度及时到达浏览器。防火墙只需放行反向代理使用的 HTTP/HTTPS 端口；`18000` 可以限制在服务器或可信网络范围内。

## 三端入口

- PC：浏览器访问 `http://localhost:18000`。
- 服务器：桌面浏览器通过 HTTPS 域名访问同一实例。
- 手机：通过 HTTPS 域名打开并使用浏览器的“添加到主屏幕”。

三端共享 `web/` 中的 UI、路由、任务状态和视觉规范。服务器目录能力由 `/api/capabilities` 控制，远程默认仅开放浏览器上传、URL 提交和受控归档访问。
