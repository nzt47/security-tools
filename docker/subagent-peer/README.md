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
