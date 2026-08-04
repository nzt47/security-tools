# 环境变量对照表（Reranker 热重载 + OMP 配置）— 快速查阅版

> 从 `RERANKER_HOT_RELOAD_DEPLOYMENT_SUMMARY.md` 与 `RERANKER_HOT_RELOAD_GUIDE.md` 提取的关键配置。
> 原则：`.env` 为唯一数据源，docker-compose 通过 `${VAR:-default}` 引用。

## 1. Reranker 热重载配置

| 变量 | 生产值 | 默认值 | 位置 | 说明 |
|------|--------|--------|------|------|
| `SKILL_RERANKER_ENABLED` | `true` | `false` | .env + compose | 总开关 |
| `SKILL_RERANKER_USE_ONNX` | `true` | `true` | .env + compose | 优先 ONNX 推理 |
| `SKILL_RERANKER_ONNX_VARIANT` | `model_quantized.onnx` | `model_quantized.onnx` | .env + compose | 初始 ONNX 变体（热重载切换目标） |
| `SKILL_RERANKER_HOT_RELOAD_INTERVAL` | `30` | `30` | .env + compose | 热重载检查间隔（秒）；`999999`≈关闭 |
| `SKILL_RERANKER_MODEL` | 见下 | 见下 | compose 固定 | 模型路径（**分环境，见 §3**） |
| `SKILL_RERANKER_TIMEOUT` | `30` | `30` | .env | 子进程超时（秒） |
| `SKILL_RERANKER_MIN_SCORE` | `0.001` | `0.001` | .env | 最低分数阈值（sigmoid 后概率） |
| `SKILL_RERANKER_RERANK_TIMEOUT` | `3.0` | `3.0` | .env | 单次推理软超时（秒），超时降级原序 |

## 2. OpenMP/MKL 线程限制（0xC0000005 预防）

| 变量 | 生产值 | 默认值 | 位置 | 说明 |
|------|--------|--------|------|------|
| `OMP_NUM_THREADS` | `4` | `4` | .env + compose | OpenMP 线程数，torch intra-op 读取 |
| `MKL_NUM_THREADS` | `4` | `4` | .env + compose | MKL 线程数 |

## 3. 模型路径（分环境，易错）

| 场景 | `SKILL_RERANKER_MODEL` 值 |
|------|--------------------------|
| Windows 本地（.env） | `C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual` |
| Docker 容器（compose 固定，不读 .env） | `/root/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual` |

> ⚠️ 容器内必须用容器路径（`/root/.cache/huggingface/...`），不能复用 Windows 路径。
> ⚠️ 模型须含 `onnx/<variant>` 文件；bge-reranker-v2-m3 容器缓存**无 ONNX**，热重载不可用。

## 4. 相关环境变量

| 变量 | 说明 |
|------|------|
| `HUGGINGFACE_CACHE_DIR` | HuggingFace 缓存宿主路径，compose 挂载到容器 `/root/.cache/huggingface` |
| `YUNSHU_FEATURE_SANDBOX` | 沙盒功能开关（默认 false） |
| `ERROR_REPORTING_*` | 错误上报配置（console/file/webhook/slack） |

## 5. 快速核对命令

```bash
# 容器内环境变量
docker exec agent-digital-life-1 env | grep -E "SKILL_RERANKER_|OMP_NUM_THREADS"

# .env 配置
Select-String -Path .env -Pattern "^(SKILL_RERANKER|OMP_NUM_THREADS|MKL_NUM_THREADS)"
```
