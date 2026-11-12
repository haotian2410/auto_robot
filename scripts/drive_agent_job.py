"""通过本地网关的作业 API 驱动一条任务，打印分步进展与终态判定。

这是浏览器页面做的事的等价复刻，也是唯一正确的端到端入口：场景真值和浏览器租约都在
网关手里，直连大脑 :8500 的 /startTask 能跑完理解和规划，但 commit 一定会被拒
（scene_lease_required）。

    WEBEDITOR_BROWSER_ID=<浏览器ID> python drive_agent_job.py "把苹果放进篮子"
    python drive_agent_job.py --browser <浏览器ID> --timeout 600 "有几个红苹果"

浏览器 ID 是本地网关为每个浏览器会话分配的标识，从前端页面 URL 的 browser_id 参数或
浏览器 devtools 的请求头 X-WebEditor-Browser-ID 里取。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8099")

# 终态记录里真正说明问题的字段，其余是回声和内部状态。
KEEP = ("id", "task_id", "executed", "agent_state", "terminal", "outcome", "reply", "metrics",
        "failure_reason", "message", "commit_receipts", "state_detail", "error", "recovery_context")


def call(gateway: str, browser: str, path: str, payload=None, method: str = "GET"):
    request = urllib.request.Request(
        gateway + path,
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers={"X-WebEditor-Browser-ID": browser, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.status, json.loads(response.read().decode())


def brief(job: dict) -> dict:
    shown = {key: job.get(key) for key in KEEP if job.get(key) is not None}
    metrics = shown.get("metrics")
    if isinstance(metrics, dict):
        metrics = dict(metrics)
        metrics.pop("model_call_details", None)  # 每轮调用的完整明细太长，看产物里的 model_calls.json
        shown["metrics"] = metrics
    return shown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", help="要执行的中文任务")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--browser", default=os.environ.get("WEBEDITOR_BROWSER_ID"))
    parser.add_argument("--timeout", type=int, default=4200, help="等待终态的秒数")
    parser.add_argument("--dry-run", action="store_true", help="只读基线场景，不提交任务")
    args = parser.parse_args()

    if not args.browser:
        parser.error("需要 --browser 或环境变量 WEBEDITOR_BROWSER_ID")

    _, world = call(args.gateway, args.browser, "/getWorld?browser_id=" + args.browser)
    print("base scene", world.get("scene_id"), "v", world.get("scene_version"),
          "objects", len(world.get("objects", [])), flush=True)
    if args.dry_run:
        return 0

    status, job = call(args.gateway, args.browser, "/api/agent/jobs",
                       {"command": args.command, "scene": world,
                        "clientRequestID": "drive-%d" % int(time.time())}, method="POST")
    print("submit", status, json.dumps(brief(job), ensure_ascii=False)[:800], flush=True)
    if "id" not in job:
        return 1

    job_id = str(job["id"])
    deadline = time.time() + args.timeout
    last = ""
    record = job
    while time.time() < deadline:
        _, record = call(args.gateway, args.browser, "/api/agent/jobs/" + job_id)
        detail = str(record.get("state_detail") or record.get("agent_state"))
        if detail != last:
            print("...", detail, flush=True)
            last = detail
        if record.get("terminal"):
            print(json.dumps(brief(record), ensure_ascii=False, indent=2))
            if record.get("plan"):
                print("PLAN", json.dumps(record["plan"], ensure_ascii=False)[:2000])
            return 0 if record.get("outcome") in (None, "success", "completed") else 2
        time.sleep(5)

    print("TIMEOUT", job_id, json.dumps(brief(record), ensure_ascii=False)[:800])
    return 3


if __name__ == "__main__":
    sys.exit(main())
