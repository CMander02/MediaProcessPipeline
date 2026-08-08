#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
WEB_DIR="$PROJECT_ROOT/web"
BIND_ADDRESS=""
PORT=18000
SERVER_MODE=0
NO_BROWSER=0
HEALTH_TIMEOUT=60
BACKEND_PID=""
BACKEND_OWNS_GROUP=0

usage() {
  cat <<'EOF'
Usage: ./scripts/start-web.sh [options]

Options:
  --server             Listen on 0.0.0.0 and require an API Token
  --no-browser         Keep the browser closed after startup
  --host ADDRESS       Override the bind address
  --port PORT          Override the port (default: 18000)
  --health-timeout SEC Health-check timeout (default: 60)
  -h, --help           Show this help
EOF
}

status() {
  printf '[MPP] %s\n' "$1"
}

is_loopback() {
  local address="${1#[}"
  address="${address%]}"
  case "$address" in
    localhost|127.0.0.1|::1) return 0 ;;
    *) return 1 ;;
  esac
}

has_api_token() {
  (
    cd "$PROJECT_ROOT"
    uv run python -c 'import json, pathlib; p=pathlib.Path("config.json"); data=json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}; raise SystemExit(0 if str(data.get("api_token", "")).strip() else 1)'
  )
}

web_build_is_current() {
  local dist="$WEB_DIR/dist/index.html"
  [[ -f "$dist" ]] || return 1
  local input
  for input in "$WEB_DIR/index.html" "$WEB_DIR/package.json" "$WEB_DIR/package-lock.json" "$WEB_DIR/vite.config.ts"; do
    [[ ! -f "$input" || ! "$input" -nt "$dist" ]] || return 1
  done
  [[ -z "$(find "$WEB_DIR/src" "$WEB_DIR/public" -type f -newer "$dist" -print -quit 2>/dev/null)" ]]
}

ensure_web_build() {
  if web_build_is_current; then
    status "前端构建已就绪。"
    return
  fi
  command -v npm >/dev/null 2>&1 || {
    printf '前端需要重新构建，请先安装 Node.js 和 npm。\n' >&2
    exit 1
  }
  status "正在构建 Web 前端..."
  (
    cd "$WEB_DIR"
    if [[ ! -d node_modules ]]; then
      npm ci
    fi
    npm run build
  )
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    status "正在停止后端服务..."
    if [[ "$BACKEND_OWNS_GROUP" -eq 1 ]]; then
      kill -TERM -- "-$BACKEND_PID" 2>/dev/null || true
    else
      kill -TERM "$BACKEND_PID" 2>/dev/null || true
    fi
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  exit "$exit_code"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER_MODE=1; NO_BROWSER=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --host) BIND_ADDRESS="${2:?--host requires an address}"; shift 2 ;;
    --port) PORT="${2:?--port requires a value}"; shift 2 ;;
    --health-timeout) HEALTH_TIMEOUT="${2:?--health-timeout requires a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || {
  printf 'Port must be between 1 and 65535.\n' >&2
  exit 2
}
[[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] && (( HEALTH_TIMEOUT >= 5 )) || {
  printf 'Health timeout must be at least 5 seconds.\n' >&2
  exit 2
}

command -v uv >/dev/null 2>&1 || {
  printf '未找到 uv，请先安装：https://docs.astral.sh/uv/\n' >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  printf '未找到 curl，健康检查需要 curl。\n' >&2
  exit 1
}

if [[ -z "$BIND_ADDRESS" ]]; then
  if [[ "$SERVER_MODE" -eq 1 ]]; then BIND_ADDRESS="0.0.0.0"; else BIND_ADDRESS="127.0.0.1"; fi
fi
if ! is_loopback "$BIND_ADDRESS" && ! has_api_token; then
  printf '服务器模式需要 API Token。请先在本机网页的“设置 → 访问控制”中配置。\n' >&2
  exit 2
fi

ensure_web_build

PROBE_HOST="$BIND_ADDRESS"
if [[ "$BIND_ADDRESS" == "0.0.0.0" || "$BIND_ADDRESS" == "::" ]]; then PROBE_HOST="localhost"; fi
PROBE_HOST="${PROBE_HOST#[}"
PROBE_HOST="${PROBE_HOST%]}"
if [[ "$PROBE_HOST" == *:* ]]; then PROBE_AUTHORITY="[$PROBE_HOST]"; else PROBE_AUTHORITY="$PROBE_HOST"; fi
HEALTH_URL="http://$PROBE_AUTHORITY:$PORT/health"
OPEN_URL="http://localhost:$PORT"

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
status "正在启动后端：$BIND_ADDRESS:$PORT"
if command -v setsid >/dev/null 2>&1; then
  (cd "$BACKEND_DIR" && exec setsid uv run python -m app.cli serve --host "$BIND_ADDRESS" --port "$PORT") &
  BACKEND_OWNS_GROUP=1
else
  (cd "$BACKEND_DIR" && exec uv run python -m app.cli serve --host "$BIND_ADDRESS" --port "$PORT") &
fi
BACKEND_PID=$!

deadline=$((SECONDS + HEALTH_TIMEOUT))
healthy=0
while (( SECONDS < deadline )); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID" || true
    printf '后端启动失败。\n' >&2
    exit 1
  fi
  if curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.35
done
[[ "$healthy" -eq 1 ]] || {
  printf '后端健康检查超时：%s\n' "$HEALTH_URL" >&2
  exit 1
}

status "Web 已就绪：$OPEN_URL"
if [[ "$NO_BROWSER" -eq 0 ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$OPEN_URL" >/dev/null 2>&1 &
  elif command -v open >/dev/null 2>&1; then
    open "$OPEN_URL" >/dev/null 2>&1 &
  fi
fi
printf '按 Ctrl+C 停止服务。\n'

set +e
wait "$BACKEND_PID"
backend_exit=$?
set -e
BACKEND_PID=""
exit "$backend_exit"
