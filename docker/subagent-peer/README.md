# 分身侧 CLI 对端镜像（container 执行后端）

`scripts/subagent_peer.py` 是 §3.10 `task_file-jsonl` 协议的参考对端；本目录给出把它
装进最小镜像的参考 Dockerfile。

## 构建与接线

    docker build -f docker/subagent-peer/Dockerfile -t yunshu-subagent-peer:1 .
    # 母体 .env / 部署配置：
    CP_SUBAGENT_CONTAINER_IMAGE=yunshu-subagent-peer:1
    CP_SUBAGENT_AGENT_CLI="python /peer/subagent_peer.py"

`container` 后端会把 `<agent_cli> -p <task_file> --output-format json --max-turns N`
包进 `docker run`（网络 none、根只读、非 root；task_file 只读挂在 `/task`，可写 tmpfs 在 `/work`）。

## 两种行为（都不假装）

| 模式 | 触发 | 行为 |
|---|---|---|
| 离线回执 | 默认 | 读 task_file，输出 `status=offline_receipt`（如实报告八要素/预算，不伪造 LLM 回答） |
| 镜内执行体 | `--handler module:callable` | 调用镜像内真实执行器（例如自带 Ollama 的 local 包装），把返回值逐行输出为 JSON Lines |

`--network none` 的容器里没有推理后端；要在镜内真跑，请把执行体及其依赖打进镜像并用
`--handler` 注入。handler 抛错 ⇒ 退出码 4 且 stdout 为空（母体按 E_UPSTREAM_FORMAT 如实记失败）。

## 实测（2026-10-10）

    python -m pytest tests/unit/test_subagent_peer_entrypoint.py -q
    # 设 CP_SUBAGENT_PEER_IMAGE=yunshu-subagent-peer:1 时包含真实 docker E2E

- 未分离挂载点时（bind 与 tmpfs 同目标 `/work`）：`docker run … cat /work/task.json`
  ⇒ `No such file or directory`（tmpfs 盖住只读 bind）—— 已修为 `/task` 只读挂载。
- 分离后：真实 `docker run` 往返 ⇒ `resolve_channel_output` 得 `tier=jsonl`、`status=offline_receipt`。

## 真推理变体（可构建；**不在本仓 CI 构建**，因为要下模型）

`Dockerfile.local` = `python:3.12-slim` + Ollama + 仓库代码 + 一个小模型（构建期拉进镜像）；
`entrypoint.local.sh` 先 `ollama serve` 再 exec 传入 argv；对端改用我们的执行体：

    docker build -f docker/subagent-peer/Dockerfile.local -t yunshu-subagent-peer-local:1 .
    CP_SUBAGENT_CONTAINER_IMAGE=yunshu-subagent-peer-local:1
    CP_SUBAGENT_AGENT_CLI="python3 /peer/subagent_peer.py --handler agent.subagent.peer_local_handler:run"
    CP_SUBAGENT_LOCAL_ENABLED=1 CP_SUBAGENT_LOCAL_ENGINE=ollama CP_SUBAGENT_LOCAL_MODEL=qwen2.5:0.5b

`agent/subagent/peer_local_handler.py` 把 task_file 交给本地 Ollama；模型没产出就**抛错**
（对端非零退出、母体记 `E_UPSTREAM_FORMAT`），绝不编造 summary。模型放 `/models`（`chmod a+rX`）、
`HOME=/work`：因为 container 后端以**非 root 65534 + 只读根**运行，默认 `/root/.ollama` 读不到。

**实测（2026-10-10）**：以 `ollama/ollama` 容器 + `qwen2.5:0.5b` 跑通 —— 容器内 peer → handler →
真实推理，产出 `status=done`、`channel_meta.llm_used=true`（退出码 0）；宿主侧同一 handler 亦通过。
可复算入口：`CP_SUBAGENT_LOCAL_E2E=1 python -m pytest tests/unit/test_peer_local_handler_ollama_e2e.py -q`
（无 Ollama 时跳过）。本仓不预置模型/镜像，故 `Dockerfile.local` 不在 CI 构建。
