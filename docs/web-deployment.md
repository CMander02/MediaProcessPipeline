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

## 优先使用桌面电脑处理任务

服务器设置 `remote_execution_enabled=true`，并将 `remote_worker_id`、`remote_worker_name` 设为优先执行电脑的 ID 和名称。桌面电脑设置 `remote_execution_worker_enabled=true`，使用已有的 `remote_server_url`、`remote_api_token` 和相同的 `remote_worker_id`。两端重启服务后生效。电脑主动连接服务器，无需开放电脑的入站端口。

新建的完整处理任务可转交执行，包括 URL 和资料库内的上传文件。电脑每 10 秒连接一次；空闲时领取一个任务，使用本机模型处理，再回传进度和包含媒体的归档。电脑忙碌时仍保持连接，后续任务继续等待。只完成下载的排队任务也可转交，电脑会重新下载 URL；服务器已开始转写或后处理的任务继续在服务器执行，保留已有进度。

电脑离线时，新任务等待 `remote_execution_wait_sec`（默认 120 秒），之后由服务器执行。已领取的任务在断线后仍归原电脑所有，重连后继续；服务器不会自动再执行一份。需要转移这类任务时，先暂停或取消旧任务，再重试。旧执行轮次不能提交结果。暂停、取消和删除操作会在电脑下次连接时生效。

桌面 MPP 服务必须保持运行。电脑关机、退出登录或停止服务后，电脑不能领取任务。此机制只提供单台优先电脑、单任务领取、断线重连和云端兜底；尚未提供多节点资源调度和跨机器检查点迁移。

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

    # Application limit: 10 GiB per file, plus room for multipart headers.
    client_max_body_size 10241m;
    client_body_timeout 300s;

    location / {
        proxy_pass http://localhost:18000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_send_timeout 300s;
        proxy_read_timeout 3600s;
    }
}
```

`proxy_buffering off` 让 SSE 任务进度及时到达浏览器。防火墙只需放行反向代理使用的 HTTP/HTTPS 端口；`18000` 可以限制在服务器或可信网络范围内。

Nginx 默认只接收 1 MiB 请求体。上例允许单文件 10 GiB，并为 multipart 请求头留出 1 MiB；实际文件上限仍由应用检查。`client_body_timeout` 和 `proxy_send_timeout` 限制相邻读写之间的等待时间，不是整个上传的总时长。`proxy_request_buffering off` 让上传直接流向后端，避免反代先将整份媒体暂存到磁盘。后端资料库仍需为上传文件预留空间；若上层代理启用了请求缓存，也需检查其上传上限与临时目录容量。

保存配置后执行 `sudo nginx -t`，通过后重新加载 Nginx。用大于 1 MiB 的文件检查上传；若收到 413，请检查所有代理层的请求体上限，再检查应用的 10 GiB 文件限制。

## 三端入口

- PC：浏览器访问 `http://localhost:18000`。
- 服务器：桌面浏览器通过 HTTPS 域名访问同一实例。
- 手机：通过 HTTPS 域名打开并使用浏览器的“添加到主屏幕”。

三端共享 `web/` 中的 UI、路由、任务状态和视觉规范。服务器目录能力由 `/api/capabilities` 控制，远程默认仅开放浏览器上传、URL 提交和受控归档访问。
