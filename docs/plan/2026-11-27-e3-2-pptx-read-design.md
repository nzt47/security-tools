# E3-2 收口（纯库路线）：pptx_read 设计与判据（2026-11-27）

> Owner 已授权「按推荐持续推进」。推荐：**不装 LibreOffice（~350MB），E3-2 收口纯库路线**，
> 补 `pptx_read` 把「读 Office 三件套」补齐（render/convert/recalculate 明确不做，理由见 §5）。
> 判据先写在这里，代码完成后逐条核对。

## 1. 形态（与 docx_read / xlsx_read 完全同套路）

| 项 | 值 |
|---|---|
| 抽取器 | `agent/office/pptx_text.py`（标准库 zipfile + ElementTree，不引 python-pptx / LibreOffice） |
| 工具 | `pptx_read`（`agent/tools/pptx_tools.py`，一个工具、一个 path 参数） |
| YAML | `data/tool_definitions/pptx_read.yaml`（description/schema 与 `@_tools.register` 逐字一致） |
| 接线 | `agent/orchestrator/lifecycle_manager.py`（与 docx/xlsx 同批 register） |
| 返回 | `{ok,path,slides,text,counts:{slides,paragraphs,tables,chars},truncated,hint}`；失败 `{ok:false,error}` |

## 2. 语义

- 幻灯片顺序**以 `ppt/presentation.xml` 的 `sldIdLst` + rels 为准**（不是文件名数字序）；
  缺 rels 才退回 `slideN.xml` 数字序，并如实标注。
- 每页取 `p:sp` 里的 `a:p`（同一段落的所有 `a:t` 拼接）与 `a:tbl` 表格；空段落跳过。
- 不含备注页（`ppt/notesSlides/`）、版式母版、图片、批注 —— 与 docx_read 的边界一致，写在 hint 里。
- 上限：页 200 / 总字符 120k / 每页段落 200；超限 `truncated=true`，**不静默截断**。
- 非 .pptx（老 .ppt 是二进制 OLE）一律拒绝并说明。

## 3. 连锁（新增工具的 6 处计数契约 + 派生）

1. `test_capability_spec`：YAML 106→107、清单 156→157、engineering 可见集 33→34（pptx 进 boost）；
2. `test_capregistry_core`：total 156→157、tool 106→107、local 132→133；
3. `test_confirm_level`：工具数 106→107；
4. `test_tool_router_hybrid_real_index_l28`：真实工具 105→106、索引 103→104；
5. `test_tool_pruning`：pptx_read risk=low ⇒ 高危误裁基线不变（不改）；
6. `test_det2_stable_sort_determinism`：code 类 19 不变（file 类 +1）；
7. `_BM25_HALF_SATURATION` 按新 calib 中位数重标定 + `eval/routing_baseline/*` 重导出 + v1 τ 复核。
派生**必须在干净 worktree** 跑（sync_tool_index / sync_capability_manifest / gen_baseline_bm25_raw /
run_routing_reliability），产物拷回主工作区。

## 4. 判据（逐条核对）

1. `tests/unit/test_pptx_read.py`：顺序以 presentation.xml 为准 / 段落与表格 / 上限 truncated /
   非 pptx / 不存在 / 坏 zip / 缺 presentation.xml / 缺 rels 的降级顺序；
2. 工具层：注册成功、缺 path 给可执行提示、`safe_resolve_path` 拒绝越界路径；
3. 6 处计数契约 + 路由冲突用例全绿；`sync_tool_index --check`、`sync_capability_manifest --check` 干净 worktree 绿；
4. `POST /api/agent-lines/preview` 实测 pptx_read 进可见集且**零丢失**（若挤掉别的工具就调 max_tools）；
5. 部署后 `/api/version` 三处一致；真机：上传一个 .pptx → `pptx_read` 读到正确页数与文字。

## 5. 明确不做

- **不装 LibreOffice**：`office_render / office_convert / office_recalculate` 不做，也不做半成品；
  理由：本机没有、CI 也没有，装 350MB 只为渲染截图，收益与维护成本不匹配（Owner 已授权按推荐收口）。
- 不写 .pptx（python-pptx 不进主依赖）；不取备注/母版/图片；不做版式还原。
