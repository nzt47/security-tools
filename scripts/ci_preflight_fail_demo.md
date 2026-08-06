# ChromaDB 预检失败 → 下游阻断：CI 面板表现模拟

> 本文档模拟 GitHub Actions 面板在 **预检故障演练**（`PREFLIGHT_FAKE_FAIL=1`）下的实际表现，
> 用于确认 `needs: [chromadb-preflight]` 阻断逻辑在 CI 上的呈现，无需真实 push 演练。
> 演练命令见 [README.md](README.md#故障演练步骤)。

---

## 演练前的改动（ci.yml 临时一行）

```yaml
      - name: 运行 ChromaDB 导入降级预检
        run: |
          PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight
```

> 容器需先构建：`docker build -t yunshu-preflight .`（或本地直接
> `PREFLIGHT_FAKE_FAIL=1 python -m agent.preflight` 验证退出码）。

---

## ① 预检 job 失败（红色 ✗）

### 面板总览

```
Summary

  ✓ 检出代码                 ── chromadb-preflight
  ✓ 设置Python环境            ── chromadb-preflight
  ✓ 安装依赖                 ── chromadb-preflight
  ✗ 运行 ChromaDB 导入降级预检  ── chromadb-preflight   ← 失败
```

### 展开失败步骤的日志

```
Run PREFLIGHT_FAKE_FAIL=1 bash scripts/chromadb_preflight.sh
  == 故障演练：PREFLIGHT_FAKE_FAIL 已设置，模拟预检失败（CI 中 unit-tests 将被 needs 阻断跳过）==
  Error: Process completed with exit code 1.
```

### Job 汇总区

```
chromadb-preflight        ❌ Failed       20s
```

---

## ② 下游 unit-tests 全部被阻断（灰色 Skipped）

```
unit-tests (matrix × 6)   ⚪ Skipped   ← 因 preflight 失败，一个都不执行
```

GitHub Actions 面板上 Skipped job 的展开信息：

```
unit-tests
  This check was skipped

  跳过原因：needs 依赖的 chromadb-preflight job 失败（或在其失败前被取消）
```

Actions 面板中 Skipped job 显示为**灰色**，不计入失败（workflow 整体显示 Success
with skipped / 或仅 preflight 标红——取决于通知配置），不会触发失败邮件/告警
（若告警规则只监听 job failure 而 skipped 不视为 failure）。

---

## 关键语义（GitHub Actions 保证）

| 环节 | 行为 |
|------|------|
| preflight job 非零退出 | job 标记 `❌ Failed` |
| `needs: [chromadb-preflight]` 的 unit-tests | 自动 `⚪ Skipped`，不执行 |
| 阻断目标 | 6 个矩阵 job 全部跳过，不消耗矩阵资源 |
| 恢复 | 移除 `PREFLIGHT_FAKE_FAIL=1` 后下次 push 恢复正常 |
