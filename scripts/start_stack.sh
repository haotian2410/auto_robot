#!/usr/bin/env bash
# 联调栈启动脚本：按依赖顺序起 模型 / 资产目录 / 网关 / 大脑，绝不杀已有进程。
#
#   bash start_stack.sh all        依次起四个服务（已在跑的端口会跳过）
#   bash start_stack.sh probe      逐个探活，全绿退出码 0
#   bash start_stack.sh status     打印四个端口的监听情况与 pid
#   bash start_stack.sh model|ols|gateway|brain
#
# 换机器时只需要改下面这一段。停服务用 Ctrl-C（前台）或按 status 里的 pid kill，
# 不要用 pkill -f "robot-brain serve"：匹配串会连当前 shell 一起杀掉。
set -uo pipefail

ROOT="${ROOT:-/home/cscvlab/lht}"
PY="${PY:-$HOME/miniconda3/envs/robot_agent_integ/bin/python}"
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/qwen3.8-27b}"
MODEL="${MODEL:-$MODEL_DIR/Qwen3.8-27B-UD-Q4_K_M.gguf}"
MMPROJ="${MMPROJ:-$MODEL_DIR/mmproj-BF16.gguf}"
CTX_SIZE="${QWEN38_CTX_SIZE:-32768}"          # 浏览器场景一轮约 21k tokens，默认 16384 会 400
LOG_DIR="${LOG_DIR:-$ROOT/var/logs}"
BRAIN_CONFIG="${BRAIN_CONFIG:-$ROOT/robot_agent_brain/config/webeditor.json}"
JOINT_INVENTORY="${JOINT_INVENTORY:-$ROOT/robot_agent_brain/var/webeditor/joint_inventory.json}"
MODEL_PORT=8080 OLS_PORT=8000 GATEWAY_PORT=8099 BRAIN_PORT=8500

mkdir -p "$LOG_DIR"

port_busy() { ss -ltn "sport = :$1" 2>/dev/null | grep -q ":$1"; }

start() { # start <name> <port> <cwd> <log> <argv...>
  local name=$1 port=$2 cwd=$3 log=$4; shift 4
  if port_busy "$port"; then
    echo "[$name] :$port 已在监听，跳过（pid: $(ss -ltnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)）"
    return 0
  fi
  [ -d "$cwd" ] || { echo "[$name] 目录不存在：$cwd" >&2; return 1; }
  echo "[$name] 启动中，日志 $log"
  ( cd "$cwd" && nohup "$@" >>"$log" 2>&1 & echo "[$name] pid $!" )
}

model() {
  [ -x "$LLAMA_SERVER" ] || { echo "[model] 找不到 $LLAMA_SERVER" >&2; return 1; }
  [ -f "$MODEL" ] && [ -f "$MMPROJ" ] || { echo "[model] 缺权重：$MODEL / $MMPROJ" >&2; return 1; }
  start model "$MODEL_PORT" "$ROOT" "$LOG_DIR/llama_server.log" \
    "$LLAMA_SERVER" --model "$MODEL" --mmproj "$MMPROJ" \
    --host 127.0.0.1 --port "$MODEL_PORT" --ctx-size "$CTX_SIZE" \
    --n-gpu-layers 999 --image-min-tokens 1024 --reasoning off
}

ols() { start ols "$OLS_PORT" "$ROOT/ObjectLibraryServer" "$LOG_DIR/ols.log" "$PY" server.py; }

gateway() {
  [ -d "$ROOT/web-frontend-master/dist" ] || echo "[gateway] 警告：没有 dist/，SPA 会是空白页，先 cd web-frontend-master && npm run build"
  start gateway "$GATEWAY_PORT" "$ROOT/web-frontend-master" "$LOG_DIR/gateway.log" \
    "$PY" local_gateway.py --port "$GATEWAY_PORT"
}

brain() {
  start brain "$BRAIN_PORT" "$ROOT" "$LOG_DIR/brain.log" \
    "$PY" "$(dirname "$PY")/robot-brain" serve \
    --config "$BRAIN_CONFIG" \
    --gateway-url "http://127.0.0.1:$GATEWAY_PORT" \
    --ols-url "http://127.0.0.1:$OLS_PORT" \
    --joint-inventory "$JOINT_INVENTORY" \
    --camera-id 5 --port "$BRAIN_PORT"
}

probe() {
  local rc=0
  curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$MODEL_PORT/health" && echo "[model] 200" || { echo "[model] DOWN"; rc=1; }
  # ObjectLibraryServer 没有 /health，用 /list 探活
  curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$OLS_PORT/list?type=model&uid=0" && echo "[ols] 200" || { echo "[ols] DOWN"; rc=1; }
  # 网关用 SPA 根路径探活：/health 会代询大脑，大脑没起时它返回 502，那不代表网关死了
  curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$GATEWAY_PORT/" && echo "[gateway] 200" || { echo "[gateway] DOWN"; rc=1; }
  curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$BRAIN_PORT/health" && echo "[brain] 200" || { echo "[brain] DOWN"; rc=1; }
  if [ "$rc" -ne 0 ]; then
    echo "--- 网关视角的中继状态："
    curl -s -m 5 "http://127.0.0.1:$GATEWAY_PORT/health" | head -c 300; echo
  fi
  return $rc
}

status() {
  local p port name pid
  for p in "$MODEL_PORT:model" "$OLS_PORT:ols" "$GATEWAY_PORT:gateway" "$BRAIN_PORT:brain"; do
    port=${p%%:*}; name=${p##*:}
    pid=$(ss -ltnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
    printf '%-8s :%-5s %s\n' "$name" "$port" "${pid:-not listening}"
  done
}

case "${1:-all}" in
  all) model; ols; gateway; brain ;;
  model|ols|gateway|brain|probe|status) "$1" ;;
  *) echo "用法: $0 {all|model|ols|gateway|brain|probe|status}" >&2; exit 64 ;;
esac
