# 本地整合输出契约（2026-11-19）

## 2026-11-21 更新说明（优先于下文历史验收记录）

- 桥接只适配接口、请求目录、产物和对象／关节映射，不判定穿透、掉落、碰撞或抓放是否物理成功；这些由控制与仿真报告。本轮没有加入位移上限检查。
- 保留接口格式、对象身份、关节反馈对应关系及许可回写字段校验，不伪造缺失产物或成功状态。
- Brain 可配置 `execution_failure_policy=discard_simulation`，仅用于独立 MuJoCo 仿真：失败不提交最终 Scene，但保留诊断轨迹。该策略不适用于实体机器人，Gateway 事务未知仍需核验。
- 8500/8099 是当前运行入口；下文 8502/8097 与平台执行结论是 11 月 19 日历史记录，不代表本轮端到端全部通过。
- 当前桥接回归 75 通过、3 跳过。控制与前端遗留问题见 Brain 仓库 `docs/recent_sessions_handover_20261121.md`。

## 历史整合记录

本轮保留 Brain 未提交修改。前端同步至 c014903；控制端本地 40fd832
包含上游 main 5e90185，尚不能用远端 main 代替本地整合版本。
原 Gateway 8098 / Brain 8501 故障会话保留；当前独立验收入口为 8097 / 8502。
OLS 已更新至 888b558（63 个模型、16 个机器人、7 个场景）。

## 职责与输入输出

- Brain：首轮平台选择、对象绑定、连续机器人操作整段规划；编辑/查询分段。
- link：为每段保存独立 request ID、commands.json、scenes/<id>/scene.json、catalog.json 和日志。
- Control：按 demo / execution / planning / collision 的上游职责执行；不能在 Brain 绕过碰撞或 IK。
- Gateway：校验会话、租约、版本及 digest 后提交实际状态，并按任务关联回放。

消费 output/summary.json、output/commands/results.jsonl、output/snapshots/state.json，
以 output/trajectories/execution.traj 回放，以验证后的 output/snapshots/final_scene.json
更新现场。不能将任意 output 文件存在当作执行成功。请求 ID 不复用，不清空历史。

主动臂/夹爪/机构的实测关节写入 RobotDriver.now_joint_positions_rad，
不能覆盖部署初始姿态。失败但最终状态可信时同步实际状态仍报告失败；
缺失证据时保留待核验，不能重启清锁或重复执行已完成步骤。

## 当前未通过项

Robotiq2F85 base 的运行时网格 hash 与上游已验证模板不匹配。
相同 AABB 不是几何等价证明，禁止用尺寸匹配代替验证或关闭碰撞。
需上游提供与模板对应的参考网格/经过验证的模板及生成环境，或完成可审计的几何等价验证。
代理模板问题仍未解决。联调显式选择控制端已支持的完整精确碰撞表示
`execution_collision_model=precise`，经桥接层传递 `--collision-model precise`，
不改变安全距离、允许碰撞对和路径检查，也不修改原始 Scene/OLS。
不在代理验证失败时自动降级。独立执行环境按上游使用 MuJoCo 3.12.0 / NumPy 2.2.6。

当前新版 OLS 下 UR10 连续两次真实模拟执行成功；中间新增物体后，上一请求末帧、
下一请求输入/首帧的机械臂及夹爪关节值相等。仍需完成合并段、编辑分段、
Piper 和抽屉的完整验收，不能宣称三项功能全部完成。

新版认证下，Brain 的机器回调统一携带 X-WebEditor-Browser-ID。
特别是 multipart /updateTraj 没有 JSON browserID，缺少此头会返回 HTTP 400。
不得通过取消认证或广播给所有浏览器解决。

本轮证据位于 /home/cscvlab/lht/var/upstream-integration/20261119-143800/。
认证证书由进程环境提供；账号密码不得写入本仓库。
