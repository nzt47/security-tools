# 会话元数据隔离机制 — 修复验证凭证归档

> 归档日期：2026-07-31（v2.1 最终复验）
> 关联缺陷：全局状态覆盖 Bug（`routes_chat` 对全局 `Yunshu` 实例的 `_session_id` 赋值在多线程并发下互相覆盖）
> 修复提交：`chat(session_id=..., session_mgr=...)` 显式传参解耦 + `_current_id` 加锁加固 + 读方法加锁（详见《会话元数据隔离机制架构设计.md》v2.1）

## 一、凭证文件清单

| 文件 | 内容 | 关键结果 |
|------|------|---------|
| [20260731_concurrent_bug_global.log](20260731_concurrent_bug_global.log) | v1 修复前时序（global 模式）复现运行输出 | 2400 次读取，**2050 次误配（85.42%）** |
| [20260731_concurrent_fix_explicit.log](20260731_concurrent_fix_explicit.log) | v1 修复后链路（explicit 模式）验证输出 | 8000 次读取，**0 次误配（0.0%）** |
| [20260731_v2_concurrent_bug_global.log](20260731_v2_concurrent_bug_global.log) | **v2 复验**：修复前时序复现 | 2400 次读取，误配 **85%+**（Bug 持续可复现） |
| [20260731_v2_concurrent_fix_explicit.log](20260731_v2_concurrent_fix_explicit.log) | **v2 复验**：修复后链路高压 | 8000 次读取，**0 次误配（0.0%）** |

## 二、测试环境与参数

- **运行命令**：
  ```
  python scripts/dev/reproduce_concurrent_session_bug.py --mode global   --users 8  --iters 300 --sleep-max-us 300
  python scripts/dev/reproduce_concurrent_session_bug.py --mode explicit --users 16 --iters 500 --sleep-max-us 300
  ```
- **复现脚本**：[reproduce_concurrent_session_bug.py](../../../scripts/dev/reproduce_concurrent_session_bug.py)（支持 `--mode global|explicit` 对照、`--output` 凭证归档）
- **压力说明**：explicit 模式为修复验证的高压档（16 并发用户 × 500 迭代 = 8000 次上下文读取），远超常规 Web 并发场景
- **判定指纹**：每个用户会话持有唯一时区 `Asia/TZ{n}`，读取结果含自己的指纹即"命中"，否则记"误配"
- **v2 复验环境**：含 SessionManager `_current_id` 加锁、DST 显式化、三层埋点的修复后代码

## 三、对照结果汇总

| 指标 | global（修复前时序） | explicit（修复后链路） |
|------|--------------------|----------------------|
| 并发用户 | 8 | 16 |
| 总读取次数 | 2400 | 8000 |
| 读到自己的上下文 | 350 | 8000 |
| 读到他人上下文（误配） | **2050** | **0** |
| 误配率 | **85.42%** | **0.0%** |
| 耗时 | 0.78s | 2.64s（v2: 2.77s） |

## 四、结论

1. **Bug 确认存在**：修复前全局赋值时序下，85.42% 的并发读取会拿到"最后写入者"的会话元数据，导致用户时区/设备/语言上下文互相串扰。
2. **修复彻底**：显式传参链路在 2 倍用户数、更高迭代数的真实压力下误配率 0.0%，v2 加固后复验结果一致，无竞态残留。
3. **向后兼容**：无参调用仍回退实例全局属性（CLI 等未接入 SessionManager 的调用方），已由 `verify_prompt_injection_mock.py` 的 18 项断言覆盖。

## 五、回归确认

- orchestrator 单元测试：130 passed / 0 failed（修复后）
- session_manager + audit 综合测试：82 passed / 0 failed（v2.1 读锁后）
- 并发读写冒烟（v2.1）：8 线程 × 100 次 add/get/get_count/get_metadata，**0 错误**（锁互斥生效，无死锁）
- 埋点链路验证 `verify_session_ctx_logs.py`：9 项断言全过
- 静态诊断（orchestrator.py / routes_chat.py / app_server.py / session_manager.py）：全部干净

## 六、复验方法

```bash
# 复现 Bug（应看到高误配率）
python scripts/dev/reproduce_concurrent_session_bug.py --mode global --users 8 --iters 300
# 验证修复（应看到误配率 0.0%）
python scripts/dev/reproduce_concurrent_session_bug.py --mode explicit --users 16 --iters 500
# 独立回归
python scripts/dev/verify_concurrent_session_fix.py
# 埋点链路
python scripts/dev/verify_session_ctx_logs.py
```
