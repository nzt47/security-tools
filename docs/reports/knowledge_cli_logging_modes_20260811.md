# 知识引擎 CLI 日志参数文档（--quiet / --verbose）

- 生成时间：2026-08-11
- 入口：`python -m agent.knowledge <子命令>`
- 适用范围：所有子命令（index-rebuild / card-list / card-transition / check-links /
  orphans / audit / resolve-conflict / import / export / list / card-from-note）

## 一、三档日志级别

每个子命令均可通过 `--verbose` / `--quiet` 控制 stderr 上的日志输出
（logging 框架输出；stdout 上的报告/结果文本不受影响）。

| 参数 | 日志级别 | 输出内容 | 典型场景 |
|---|---|---|---|
| `--quiet` | ERROR | 仅错误（如迁移失败、导入失败） | CI 静默跑通，只看失败原因 |
| （无参数） | WARNING | 汇总性问题（断链/孤儿/漂移/过期/矛盾 各一条批量汇总） | 默认；问题可见但不刷屏 |
| `--verbose` | INFO | 全部 INFO（各模块耗时统计、断链明细、检测明细） | 运行时排查、性能分析 |

优先级规则：`--quiet` 优先于 `--verbose`。二者同时给出时以 `--quiet` 为准
（避免问题库上明细刷屏）。

## 二、参数行为说明

### 2.1 `--quiet`（仅 ERROR）

```powershell
python -m agent.knowledge audit --wiki knowledge/wiki --no-email --quiet
```

- stderr 只打印 ERROR 级日志（如 `CLI card-transition: 迁移失败`、`导入失败`）。
- warning 级的问题汇总（断链/孤儿等）**不打印**；问题是否命中改由 stdout
  的「巡检完成」结论行与退出码判断。
- 适用：CI 门禁 job——日志量最小化，只有失败才暴露错误。

### 2.2 默认（WARNING）

```powershell
python -m agent.knowledge audit --wiki knowledge/wiki --no-email
```

- stderr 打印 WARNING 及以上：lint 五类检测命中时各一条批量汇总
  （`lint_all[孤儿]`、`lint_all[断链]` 等，单条含数量与明细列表）。
- 断链等**逐条明细已降为 debug**（P0 日志治理），默认模式下不会出现
  「每条断链一行」的刷屏。

### 2.3 `--verbose`（INFO）

```powershell
python -m agent.knowledge audit --wiki knowledge/wiki --no-email --verbose
```

- 打开 INFO：含 `[workflow] Step5 耗时明细 检测=… 计算=… 报告=…`、
  `lint_all[耗时汇总]`、`find_broken_links: 扫描卡片=…` 等逐模块明细。
- 断链逐条明细（debug）需要时可临时调整日志级别为 DEBUG（在代码/测试内
  通过 `logging` 配置开启），`--verbose` 只覆盖到 INFO。

## 三、行为示例（audit 子命令，含 1 条断链的库）

| 参数 | stderr+stdout 总行数 | stderr 是否含 WARNING | stderr 是否含 INFO |
|---|---|---|---|
| 默认 | 5 | 是（断链批量汇总 1 条） | 否 |
| `--quiet` | 2 | 否 | 否 |
| `--verbose` | >5 | 是 | 是（耗时明细等） |

> 实测口径：`python -m agent.knowledge audit --wiki <tmp> --index <tmp>/index.md
> --reports-dir <tmp>/r --no-email --json <tmp>/out.json [--quiet|--verbose]`。

## 四、CI 推荐用法

```yaml
# .github/workflows/ci.yml · knowledge-audit-smoke 同口径
- name: 知识库健康审计
  run: |
    python -m agent.knowledge audit --wiki knowledge/wiki \
      --index knowledge/index.md --reports-dir data/knowledge/reports \
      --no-email --json data/knowledge/reports/audit.json --quiet
```

- `--quiet`：正常库近乎零日志；审计问题只影响 JSON 产物与退出码，日志不淹没 CI。
- 需要定位问题时临时追加 `--verbose` 重跑即可，无需改代码。

## 五、相关实现

- 参数注册与分级：`agent/knowledge/__main__.py`（`build_parser` 每个子命令
  均注册 `--verbose`/`--quiet`；`main()` 按 quiet → 默认 → verbose 三档配置
  `logging.basicConfig`）。
- 断链逐条明细降级：`agent/knowledge/links.py` `find_broken_links`
  （逐条 `logger.warning` → `logger.debug`，批量汇总保留在 `lint.py`）。
- 退出码契约（不易）：0 = 成功；1 = 出错（卡片不存在 / 非法迁移 / 检出断链 / 裁决失败），
  不受日志级别影响。
