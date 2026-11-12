# auto_robot —— 中文任务驱动的机器人联调整合栈

一条中文指令（"把最右边的苹果放到绿色苹果的上方"）在这里被逐层翻译成**场景编辑**或**机械臂原子动作**，
在 MuJoCo 里真跑，再把结果写回浏览器里的三维场景。本仓库是这套多进程系统的**整合记录**：总纲文档、
启动与驱动脚本、以及唯一尚未托管在任何远端的自研胶水层 `robot_agent_link`。

四个组件仓库本体不在这里复制，按 [`MANIFEST.json`](MANIFEST.json) 里的 URL + commit 锁定；
资产库数据（417 MB mesh）、模型权重（17 GB gguf）、`node_modules`、运行产物同样不入库，
取法见 [docs/stack_runbook.md](docs/stack_runbook.md) 第 1-3 节。

> 文档与脚本中的路径是这套系统实际运行的那台机器（Linux x86-64，双 RTX 4090 D）的实测值，
> 不是设想。换机器时按 [docs/stack_runbook.md](docs/stack_runbook.md) 的探活表逐项核对。

---

## 1. 一句话的完整旅程

```
 浏览器 SPA (:8099 dist/)
   │  POST /api/agent/jobs {command}            ← 唯一正确的驱动入口
   ▼
 local_gateway.py  jobs() ................ local_gateway.py:1532
   │  AgentRelay.start_task → POST http://127.0.0.1:8500/startTask
   ▼
 ┌──────────────────────── 大脑 robot-brain serve (:8500) ────────────────────────┐
 │ web/service.py execute 线程 ......................................... :1187     │
 │  ① 理解   provider → BrainTurn；路由纠偏 + domain_policy.classify    pipeline:434│
 │  ② 拆分   decompose（"分一个做一个"，只在值得拆时拆）                pipeline:441│
 │  ③ 分派   SCENE_EDIT → SceneEditor ................. pipeline:486               │
 │           SCENE_QUERY → SceneQueryEngine ........... pipeline:495               │
 │           ROBOT_TASK → SceneGrounder ............... pipeline:538               │
 │  ④ 规划   plan_atomic → AtomicCommandsFile 2.0 ..... pipeline:576               │
 │  ⑤ 导出   artifacts["commands"] = commands.json .... adapters/artifact_writer:53│
 └─────────────────────────────────────────────────────────────────────────────────┘
   │  dispatch() 读回 commands.json ..................... web/service.py:679
   ▼
 SubprocessExecutionAdapter → link.runner → subprocess `python -m link.control_child`
   │  runpy 控制仓 demo.run_demo，mujoco 后端 + libsim_platform_c.so
   ▼  读回 report.json / trajectory.traj → link.feedback.summarize → ExecutionOutcome
 ┌──────────────────────── 回写阶段（大脑发起，网关落库）────────────────────────┐
 │ 成功：executed_scene 重打位姿 (service:739) → POST /commitWorld (gateway:272) │
 │ 失败：writeback_status="none"，场景不动，只推轨迹                              │
 │ 轨迹：租约释放后 push_trajs → POST /updateTraj (gateway:108)                   │
 └────────────────────────────────────────────────────────────────────────────────┘
   │  GET /api/agent/jobs/<id> 轮询到 terminal
   ▼
 浏览器播放轨迹 + 场景更新
```

三条铁律，踩过就知道疼：

1. **场景真值在网关，不在大脑。** 大脑必须先 `POST /acquireSceneLease` 拿到 `X-Brain-Lease`
   才能 `commitWorld`（申请在 `robot_agent_brain/src/robot_agent_brain/web/gateway.py:80`，
   校验在 `web-frontend-master/local_gateway.py:2751`，不过就抛 `scene_lease_required` `:2811`）。
   绕开网关直连 `:8500` 能在理解/规划阶段跑通，但 commit 一定被拒 —— 所以端到端一律走
   `/api/agent/jobs`。
2. **几何、碰撞、可达性归控制端。** 大脑只产出语义正确的原子命令，不校验物理可行性，
   也不替控制端"修参数让它过"。
3. **执行失败推轨迹、不改场景。** 失败轨迹是证据，不是要抹掉的痕迹。

## 2. 组件与端口

| 服务 | 端口 | 仓库 | 角色 |
|---|---|---|---|
| `llama-server` Qwen3.8-27B（视觉+文本） | `127.0.0.1:8080` | gguf 本地权重 | 理解、拆分、原子规划、控制端感知共用同一个模型服务 |
| ObjectLibraryServer | `0.0.0.0:8000` | `tianlanqqcom/ObjectLibraryServer` | 资产目录（mesh/元数据/场景模板），无 `/health`，探活打 `/list?type=model&uid=0` |
| web-frontend `local_gateway.py` | `0.0.0.0:8099` | `tianlanqqcom/web-frontend` | 场景真值 + 浏览器 WS + SPA（`dist/`）+ 大脑作业 API + 租约仲裁 |
| `robot-brain serve` | `127.0.0.1:8500` | `haotian2410/robot_agent_brain` | 中文 → `BrainTurn` → grounding → 原子命令 → 执行编排 → 写回 |
| 控制端（子进程，无常驻端口） | — | `Leon-J1n/Auto_Robot_demo` | 原子技能解释器 + MuJoCo 物理（`_thirdparty/SimPlatform-main/build/libsim_platform_c.so`） |
| `robot_agent_link`（本仓库 `glue/`） | — | **仅存于本仓库** | 大脑↔控制端进程桥：暂存工作区、子进程 runner、感知改道本地 Qwen、控制端中文错误句抽取 |

`robot_agent_link` 各模块：

| 文件 | 干什么 |
|---|---|
| [link/runner.py](glue/robot_agent_link/link/runner.py) | `subprocess.Popen` 起 `python -m link.control_child`，收 `report.json`/`trajectory.traj` |
| [link/control_child.py](glue/robot_agent_link/link/control_child.py) | 子进程入口，`runpy` 执行控制仓 `demo.run_demo`，注入环境变量 |
| [link/workspace.py](glue/robot_agent_link/link/workspace.py) | 每次运行一个独立暂存目录，互不覆盖 |
| [link/qwen_vlm.py](glue/robot_agent_link/link/qwen_vlm.py) | 把控制端 VLM 感知改道到本地 Qwen，零改动控制端 |
| [link/error_descriptions.py](glue/robot_agent_link/link/error_descriptions.py) | 从控制端 `demo/run_demo.py` 抽错误码→中文释义，失败回复据此说话 |
| [link/feedback.py](glue/robot_agent_link/link/feedback.py) | `report.json` → `ExecutionOutcome`（成功/失败/部分 + 证据句） |
| [scripts/run_once.py](glue/robot_agent_link/scripts/run_once.py) | 脱离大脑单跑一次抓放，验证控制端与物理库 |

## 3. 快速启动

按依赖顺序起，模型最慢（加载 gguf 约数分钟）：

```bash
export QWEN38_CTX_SIZE=32768          # 脚本默认 16384，一轮浏览器场景约 21k tokens，会被截
bash scripts/start_stack.sh all        # 依次起 模型 / OLS:8000 / 网关:8099 / 大脑:8500
bash scripts/start_stack.sh probe      # 四个端口逐个探活，全 200 才算起来
```

手工命令与逐项解释见 [docs/stack_runbook.md §2](docs/stack_runbook.md)。停服务**只按 pid**
（`ss -ltnp | grep ':8500'` 取 pid 再 `kill`）：`pkill -f "robot-brain serve"` 的匹配串会连
当前 shell 一起杀掉。大脑是 editable 安装，改源码重启即可，不用重新打包。

驱动一条任务：

```bash
python scripts/drive_agent_job.py "把最右边的苹果放到绿色苹果的上方"
```

脚本做的事和浏览器完全一致：带 `X-WebEditor-Browser-ID` 头 `GET /getWorld` 取基线 →
`POST /api/agent/jobs` → 轮询 `GET /api/agent/jobs/<id>` 到 `terminal`，然后打印
`outcome / agent_state / commit_receipts / 每步 verdict / failure_reason`。

## 4. 配置

运行时配置是一份 JSON：[`config/webeditor.json`](config/webeditor.json)（本仓库带一份实测快照）。
优先级 **命令行 > `ROBOT_BRAIN_*` 环境变量 > `--config` JSON > 包内默认**。关键项：

- `provider`/`planner` = `qwen`，`base_url` = `http://127.0.0.1:8080/v1`，`timeout` = 300。
- `asset_library_root` —— 大脑按**文件**读目录索引，同时按 `--ols-url` 走 `/list` 校验一致性；
  两侧 `no_display` 标记必须一致，否则整批资产库测试红。
- `default_table_metadata_ref` / `default_robot_metadata_ref` / `default_scene_edit_assets`（"新增 X"的资产兜底表）。
- 执行开关五件套：`execution_enabled=true` 是**唯一**授权位，在派发入口校验；
  另四项给出解释器、控制仓根、物理 `.so`、link 根。
- `release_policy` 三个 `false`：禁止动态换平台、运行期换机械臂、自然语言首轮选平台。
- `debug=true` 才落 `debug/*` 产物；`output_dir` 相对大脑 cwd。

## 5. 语义选择规则（为什么这句选中了它）

Query / SceneEdit / RobotTask 三条路由**共用同一个** `SemanticEntitySelector`，规则一致：

- **名称与别名优先，tags 是主匹配信号，`semantic_name` 只用于展示。** 名字与别名都落空时，
  再按 tag 重叠打分拓宽一次候选池取最高分集合；拓宽只能扩大"找不到"，不会改变已有命中。
- **颜色是软偏好（policy v3）：永远不因"匹配不到颜色"而报错。** 有证据按证据筛；整批实例都没
  声明颜色时丢掉颜色词、只按主体重选；系统表达不出来的颜色词（如「彩色」）与读不出来的
  贴图色值一律当作"无证据"。
- **两个刻意保留的护栏**：某实例确实声明过别的颜色时，"矛盾"照样排除它（染过色的场景里
  「有几个红苹果」答 0）；查询路由与 `quantity_mode=all`/显式 count 不做描述退化 ——
  退化只在地面（grounding）路径打开，由 `allow_description_fallback` 控制。
- 完整规则（含缩放、`color_override`、包围盒支撑事实）见大脑仓库
  `docs/scene_scale_color.md` 与 `docs/contracts.md`。

## 6. 复合任务的分步执行

`decompose` 判定值得拆才拆（`pipeline.py:441`），编排器 `_run_steps`（`web/service.py:528`）
**分一个做一个**：每步独立走完 理解→grounding→规划→执行→写回，上一步的真实世界结果
（`executed_scene`）作为下一步的场景基线，因此跨步指代（"它"）指向的是上一步真正落下的那个对象。

- 每步终态：`completed` / `stopped` / `not_run`；`stop_reason` 取值含
  `plan_not_executed`、`execution_writeback_missing`、`arm_still_holding`、`clarification_required`、`failed`。
- 抓取类失败可在同一步重试：`RETRYABLE_EXECUTION_CODES`（`service.py:468`）+
  `MAX_STEP_ATTEMPTS = 5`（`:472`）。
- 任一步停止即中断后续步骤，回复按步汇总（`composite_result`），失败句直接引用控制端的
  中文错误释义而不是大脑自己编造。

## 7. 产物与排查

每个请求一个目录，先落产物再发布，不覆盖旧请求：

```
<output_dir>/<sessionID>/<requestID>/
  request_record.json  result.json  scene_config.json
  commands.json                    原子命令（AtomicCommandsFile 2.0），robot_task 才有
  query_result.json                查询路由才有
  debug/brain_turn.json            本轮 turn_kind / status / model payload
  debug/task_intent.json           有分解时
  debug/grounded_task.json         grounding 结果（选中的 scene_object_id 集合）
  debug/planner_context.json       规划输入（技能目录 + 语义摘要）
  debug/raw_skill_plan.json        模型原始回答
  debug/normalized_skill_plan.json 规范化后的计划
  debug/skill_plan_validation.json 校验结论（失败原因在这里）
  debug/semantic_protocol.json     语义协议快照
  debug/raw_model_response.txt     {"turn_id": <本 request id>, "<stage>": 原始文本}
  debug/model_calls.json           prompt_sha256 / token 数 / 耗时
  debug/traceback.txt              只在抛异常时存在，直接给行号
<output_dir>/<sessionID>/gateway_transactions/*.json   网关 commit 回执（租约问题看这里）
<output_dir>/<sessionID>/traj_playback/                执行轨迹（含失败轨迹）
<output_dir>/<sessionID>/original_scene*.json          会话初始场景与清单
```

`raw_model_response.txt` 按轮隔离：出现陌生 stage 或跨轮重复 blob 即为回归。

## 8. 测试

```bash
cd robot_agent_brain && <integ-python> -m pytest brain_tests -q
```

基线是 **9 个资产库相关失败**（环境性：断言里的资产号不在当前 revision 的目录里），
名单见 [docs/stack_runbook.md §6](docs/stack_runbook.md)；新失败要和这 9 个区分开。
胶水层自带 48 条测试，用 `tests/fake_control` 替身，不需要真控制端也能跑通：

```bash
cd glue/robot_agent_link && <integ-python> -m pytest tests -q     # 48 passed
```

## 9. 当前状态与已知边界

已闭合：单步与复合任务的浏览器端到端闭环、执行失败推轨迹不改场景、跨步指代、
颜色策略 v3、原子方言九技能与 `put` 侧向/上下落点、单臂平台门禁按臂数而非型号。

仍开放：

- **模型层截住颜色句**。大脑的退化阶梯跑不到，因为 Qwen 在理解阶段就自己返回
  `clarification_required` 或把颜色词丢掉（`entities=[]`、`color=null`）。这是理解 prompt 的
  问题，属项目维护方，代码侧无法绕过。
- **控制端能力下限**：`put` 需要一个对象作落点；抓取半径实测约 18.6 cm；
  高台放置会碰 `PLACEMENT_HEIGHT_SEARCH_FAILED`。几何与物理不在大脑修。
- **多机械臂步骤**尚未逐步承接执行世界。
- 同名不同类的资产会互相竞争（同一份数据里 4 个"苹果"分属 `graspable` 与 `fruit` 两类，
  目录里还有个挂在 `button` 类下的青苹果），选不准时先查数据而不是查算法。

## 10. 仓库结构

```
auto_robot/
├── README.md                  本文：整合总纲
├── MANIFEST.json              四个组件仓库的 URL / 分支 / commit / 脏度 + 本机环境指纹
├── docs/stack_runbook.md      启动与交接手册（实测值）
├── scripts/
│   ├── start_stack.sh         按序起四个服务，`probe` 子命令逐个探活
│   └── drive_agent_job.py     走网关作业 API 驱动一条任务并打印终态
├── config/webeditor.json      大脑运行时配置快照（本机绝对路径，照抄需改）
└── glue/robot_agent_link/     自研胶水层（尚未托管在任何远端）
    ├── link/                  子进程 runner、工作区、感知改道、错误释义、反馈归一
    ├── scripts/run_once.py    脱离大脑单跑一次抓放
    └── tests/                 用 tests/fake_control 替身，不需要真控制端
```

## 11. 相关文档

- [docs/stack_runbook.md](docs/stack_runbook.md) —— 交接手册：进程快照、照抄可用的启动命令、
  配置逐项、端到端驱动规则、产物布局、测试基线、操作禁忌。
- `robot_agent_brain/docs/contracts.md` —— 团队 Scene v1 与规划协议契约全文。
- `robot_agent_brain/docs/scene_scale_color.md` —— 缩放与颜色的证据规则。
- `robot_agent_brain/docs/webeditor_integration.md` —— 大脑与网关的接口细节。
- [MANIFEST.json](MANIFEST.json) —— 四个组件仓库的 URL、分支、commit 与工作区脏度。
