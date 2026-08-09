# Docker kwarg 扫描误报修复方案（2026-08-04）

> 关联：遗留待办 `todo_followup_20260804.md` §1、BOM 修复总结 `bom_fix_links_cleanup_summary_20260803.md` §四/§五
> 状态：方案待评审 → 评审通过后执行
> 优先级：**P0**（CI 永远红灯，阻断 develop 提交）

---

## 一、问题陈述

**现象**：`关键字参数冲突扫描 (Docker)` workflow（run 30817413422，develop @ 4db85572）报「52 HIGH 风险，CI 阻断」。

**复核结论**：在 develop 4db85572 的 `agent/` 子目录本地复现扫描，**375 文件、52 findings 全部为 LOW**（0 HIGH / 0 MEDIUM）。Docker 报「52 HIGH」是误报。

**影响**：
- develop 分支每次推送/PR 都被此 workflow 阻断，开发者无法通过 CI 门禁
- 误报噪声掩盖真实风险，削弱扫描器公信力

---

## 二、根因分析（误报链）

### 2.1 权限不变量证据

[Dockerfile](../../packages/kwarg_scanner/Dockerfile) 关键配置：

```dockerfile
# L37: 创建非 root 用户
RUN groupadd -r scanner && useradd -r -g scanner -d /home/scanner -m scanner
# L44: 工作目录即 CI 挂载点
WORKDIR /project
# L59: 切换到非 root 用户
USER scanner
```

[kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml) L122-129 挂载与输出配置：

```yaml
docker run --rm \
  -v "${{ github.workspace }}:/project" \           # 宿主目录挂载到 /project
  -e OUTPUT_FILE=/project/kwarg-high-risk-report.json \  # 报告写到挂载点根目录
  ...
```

**冲突**：GitHub Actions runner 上 `github.workspace` owner 是 runner 用户（UID ~1001），容器内 `scanner` 是系统用户（UID < 1000）。`scanner` 对挂载的 `/project` 根目录无写权限 → `PermissionError: [Errno 13] Permission denied: '/project/kwarg-high-risk-report.json'`。

### 2.2 exit code 错误映射

[docker-entrypoint.sh](../../packages/kwarg_scanner/docker-entrypoint.sh) L249-259：

```bash
1)
    log_json "scan_complete" result "blocked" reason "high_risk_detected" ...
    exit 1
    ;;
```

**缺陷**：`case 1)` 分支无条件把 exit 1 映射为 `high_risk_detected`。但 Python 进程因 `PermissionError` 崩溃时也 exit 1，导致「进程崩溃」被误判为「发现 HIGH 风险」。

### 2.3 误报链全貌

```
宿主 workspace 挂载到 /project（owner=runner, 容器内 scanner 无写权限）
        ↓
扫描器尝试写 /project/kwarg-high-risk-report.json
        ↓
PermissionError → Python 进程 exit 1
        ↓
entrypoint case 1) 无条件映射为 high_risk_detected
        ↓
CI 报「52 HIGH 风险，阻断」（实为 0 HIGH）
```

---

## 三、修复方案

### 修改点 1：kwarg-docker-scan.yml — 输出路径迁移

**目标**：让 `scanner` 用户能写报告文件。

**方案**：在宿主预创建 777 临时目录，挂载为容器内 `/output`；`OUTPUT_FILE` 指向 `/output`；`/project` 改为只读挂载（最小权限）。

**影响 job**：`high-risk-scan`（L116-138）、`medium-risk-scan`（L170-180）。`custom-scan` 不写文件，无需改。

#### 1.1 high-risk-scan 修改

**Before**（L116-138）：

```yaml
      - name: 运行 HIGH 风险扫描
        id: scan
        run: |
          echo "=== Docker 容器内运行 HIGH 风险扫描 ==="
          # 挂载工作区到 /project，容器内扫描 /project/agent
          # 结构化日志输出到 stderr，扫描报告输出到 stdout
          docker run --rm \
            -v "${{ github.workspace }}:/project" \
            -e MIN_RISK=HIGH \
            -e OUTPUT_FORMAT=json \
            -e OUTPUT_FILE=/project/kwarg-high-risk-report.json \
            -e ENABLE_LOGGING=true \
            ${{ needs.prepare-image.outputs.image }} \
            --path /project/agent 2>&1 | tee docker-scan-high.log
          echo "exit_code=${PIPESTATUS[0]}" >> $GITHUB_OUTPUT

      - name: 判断扫描结果
        if: steps.scan.outputs.exit_code != '0'
        run: |
          echo "::error::Docker 扫描检测到 HIGH 风险，CI 已阻断 (exit=${{ steps.scan.outputs.exit_code }})"
          echo "::error::查看报告: kwarg-high-risk-report.json"
          echo "::error::修复提示: 在 **kwargs 展开前过滤保留键，使用 safe_ 前缀命名变量"
          exit 1
```

**After**：

```yaml
      - name: 运行 HIGH 风险扫描
        id: scan
        run: |
          echo "=== Docker 容器内运行 HIGH 风险扫描 ==="
          # 【不易】容器内 scanner 用户对宿主挂载根目录无写权限,
          #   报告必须写入预创建的 777 临时目录(挂载为 /output)。
          # 【变易】/project 改为只读挂载(:ro),符合最小权限原则。
          mkdir -p scan-output && chmod 777 scan-output
          docker run --rm \
            -v "${{ github.workspace }}:/project:ro" \
            -v "${{ github.workspace }}/scan-output:/output" \
            -e MIN_RISK=HIGH \
            -e OUTPUT_FORMAT=json \
            -e OUTPUT_FILE=/output/kwarg-high-risk-report.json \
            -e ENABLE_LOGGING=true \
            ${{ needs.prepare-image.outputs.image }} \
            --path /project/agent 2>&1 | tee docker-scan-high.log
          # 【简易】报告落到宿主 scan-output/ 下,直接读取
          if [ -f scan-output/kwarg-high-risk-report.json ]; then
            cp scan-output/kwarg-high-risk-report.json kwarg-high-risk-report.json
          fi
          echo "exit_code=${PIPESTATUS[0]}" >> $GITHUB_OUTPUT

      - name: 判断扫描结果
        if: steps.scan.outputs.exit_code != '0'
        run: |
          # 【不易】exit!=0 需区分: exit 1 = 真有 HIGH(报告为证); exit 3 = 扫描器异常
          if [ "${{ steps.scan.outputs.exit_code }}" = "1" ] && [ -f kwarg-high-risk-report.json ]; then
            HIGH_COUNT=$(python3 -c "
import json
try:
    d = json.load(open('kwarg-high-risk-report.json'))
    print(d.get('summary', {}).get('HIGH', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
            echo "::error::Docker 扫描检测到 ${HIGH_COUNT} 处 HIGH 风险,CI 已阻断"
            echo "::error::查看报告: kwarg-high-risk-report.json"
            echo "::error::修复提示: 在 **kwargs 展开前过滤保留键,使用 safe_ 前缀命名变量"
            exit 1
          else
            echo "::error::Docker 扫描器异常 (exit=${{ steps.scan.outputs.exit_code }}),请查看 docker-scan-high.log"
            exit 1
          fi
```

#### 1.2 medium-risk-scan 修改（同模式）

**Before**（L170-180）：

```yaml
      - name: 运行 MEDIUM 风险扫描
        run: |
          echo "=== Docker 容器内运行 MEDIUM 风险扫描 ==="
          docker run --rm \
            -v "${{ github.workspace }}:/project" \
            -e MIN_RISK=MEDIUM \
            -e OUTPUT_FORMAT=json \
            -e OUTPUT_FILE=/project/kwarg-medium-risk-report.json \
            -e ENABLE_LOGGING=true \
            ${{ needs.prepare-image.outputs.image }} \
            --path /project/agent 2>&1 | tee docker-scan-medium.log || true
```

**After**：

```yaml
      - name: 运行 MEDIUM 风险扫描
        run: |
          echo "=== Docker 容器内运行 MEDIUM 风险扫描 ==="
          mkdir -p scan-output && chmod 777 scan-output
          docker run --rm \
            -v "${{ github.workspace }}:/project:ro" \
            -v "${{ github.workspace }}/scan-output:/output" \
            -e MIN_RISK=MEDIUM \
            -e OUTPUT_FORMAT=json \
            -e OUTPUT_FILE=/output/kwarg-medium-risk-report.json \
            -e ENABLE_LOGGING=true \
            ${{ needs.prepare-image.outputs.image }} \
            --path /project/agent 2>&1 | tee docker-scan-medium.log || true
          [ -f scan-output/kwarg-medium-risk-report.json ] && \
            cp scan-output/kwarg-medium-risk-report.json kwarg-medium-risk-report.json || true
```

#### 1.3 medium-risk-scan 统计步骤路径同步

L184 `if [ -f kwarg-medium-risk-report.json ]` 无需改（1.2 已 cp 到宿主根目录）。

#### 1.4 上传 artifact 步骤无需改

L145-148 / L203-207 的 `path:` 仍引用 `kwarg-high-risk-report.json` / `kwarg-medium-risk-report.json`，已通过 cp 落到 workspace 根目录，保持兼容。

### 修改点 2：docker-entrypoint.sh — exit 1 加证据校验

**目标**：即便权限再次出错，也不会把「进程崩溃」误判为「HIGH 风险」。

**Before**（L249-259）：

```bash
    1)
        log_json "scan_complete" \
            result "blocked" \
            exit_code "$SCAN_EXIT_CODE" \
            total_duration_ms "$TOTAL_DURATION_MS" \
            reason "high_risk_detected"
        trackEvent "scan_blocked" "{\"duration_ms\":$TOTAL_DURATION_MS,\"reason\":\"high_risk_detected\"}"
        echo "[CI] 扫描阻断: 发现 HIGH 风险，请修复后再提交" >&2
        echo "[CI] 提示: 在 **kwargs 展开前过滤保留键，使用 safe_ 前缀命名变量" >&2
        exit 1
        ;;
```

**After**：

```bash
    1)
        # 【不易】exit 1 必须有「报告存在且 HIGH 计数 > 0」作证,否则视为扫描器异常崩溃
        #   (如 PermissionError/OOM/段错误),不能误判为 high_risk_detected
        HIGH_COUNT="0"
        if [ -n "$OUTPUT_FILE" ] && [ -f "$OUTPUT_FILE" ]; then
            HIGH_COUNT=$(python3 -c "
import json
try:
    d = json.load(open('$OUTPUT_FILE'))
    print(d.get('summary', {}).get('HIGH', 0))
except Exception:
    print(0)
" 2>/dev/null || echo "0")
        fi

        if [ "$HIGH_COUNT" -gt 0 ] 2>/dev/null; then
            log_json "scan_complete" \
                result "blocked" \
                exit_code "$SCAN_EXIT_CODE" \
                total_duration_ms "$TOTAL_DURATION_MS" \
                reason "high_risk_detected" \
                high_risk_count "$HIGH_COUNT" \
                report_file "$OUTPUT_FILE"
            trackEvent "scan_blocked" "{\"duration_ms\":$TOTAL_DURATION_MS,\"reason\":\"high_risk_detected\",\"high_count\":$HIGH_COUNT}"
            echo "[CI] 扫描阻断: 发现 $HIGH_COUNT 处 HIGH 风险,请修复后再提交" >&2
            echo "[CI] 提示: 在 **kwargs 展开前过滤保留键,使用 safe_ 前缀命名变量" >&2
            echo "[CI] 报告: $OUTPUT_FILE" >&2
            exit 1
        else
            # 【变易】exit 1 但无有效 HIGH 报告 → 扫描器异常,不阻断为 HIGH
            die "E_SCAN_CRASHED" \
                "扫描器 exit 1 但无有效 HIGH 风险报告(report=$OUTPUT_FILE, high_count=$HIGH_COUNT),疑似进程崩溃或权限错误" \
                3
        fi
        ;;
```

**关键变更点**：
1. 检查 `OUTPUT_FILE` 是否存在
2. 用 Python（镜像内必有）解析 `summary.HIGH` 计数
3. `HIGH > 0` 才 `exit 1`（真实阻断）；否则 `die` 抛 `E_SCAN_CRASHED` 并 `exit 3`
4. 日志补 `high_risk_count` / `report_file` 字段，便于审计

### 修改点 3（可选，P2）：扫描器 stdout 报告校验

若担心 `OUTPUT_FILE` 写入成功但内容异常，可在 entrypoint 的 `case 1)` 校验里追加 `findings` 数组长度与 `summary.HIGH` 一致性检查。本次修复**不纳入**，留作后续增强。

---

## 四、验证步骤

### 4.1 本地验证（修改后、推送前）

**步骤 1：构建镜像**

```powershell
cd c:\Users\Administrator\agent
docker build -t kwarg-scanner:fix-test ./packages/kwarg_scanner
```

**步骤 2：复现修复前误报（确认根因）**

```powershell
# 模拟 CI 挂载:宿主目录只读给容器,报告写挂载根目录(应 PermissionError)
docker run --rm `
  -v "${PWD}:/project" `
  -e MIN_RISK=HIGH `
  -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/project/test-before.json `
  kwarg-scanner:fix-test --path /project/agent
echo "exit_code=$LASTEXITCODE"
# 期望:exit 1,stderr 含 PermissionError(误报链复现)
```

**步骤 3：验证修复后行为**

```powershell
# 模拟修复后挂载:预创建 777 输出目录,/project 只读
mkdir -p scan-output; chmod 777 scan-output
docker run --rm `
  -v "${PWD}:/project:ro" `
  -v "${PWD}/scan-output:/output" `
  -e MIN_RISK=HIGH `
  -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/output/kwarg-high-risk-report.json `
  -e ENABLE_LOGGING=true `
  kwarg-scanner:fix-test --path /project/agent 2>&1 | Tee-Object docker-scan-high.log
echo "exit_code=$LASTEXITCODE"
# 期望:
#   - exit 0(因 develop agent/ 实测 0 HIGH)
#   - scan-output/kwarg-high-risk-report.json 存在,summary.HIGH=0
#   - stderr 日志含 scan_complete result=success
```

**步骤 4：验证 entrypoint 防御逻辑（注入故障）**

```powershell
# 故意把 OUTPUT_FILE 指回不可写路径,验证 entrypoint 不再误判 HIGH
docker run --rm `
  -v "${PWD}:/project:ro" `
  -e MIN_RISK=HIGH `
  -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/project/should-fail.json `
  kwarg-scanner:fix-test --path /project/agent 2>&1
echo "exit_code=$LASTEXITCODE"
# 期望:
#   - exit 3(不再 exit 1)
#   - 日志含 E_SCAN_CRASHED,reason 不是 high_risk_detected
#   - CI 不会误报 HIGH 阻断
```

**步骤 5：JSON 报告字段校验**

```powershell
python -c "import json; d=json.load(open('scan-output/kwarg-high-risk-report.json')); print('summary:', d.get('summary')); print('findings_count:', len(d.get('findings', [])))"
# 期望:summary.HIGH=0, findings_count=52(全 LOW)
```

### 4.2 CI 验证（推送后）

**步骤 1**：提交修改到 develop 分支，推送后触发 `关键字参数冲突扫描 (Docker)` workflow。

**步骤 2**：在 Actions 页面确认：
- `high-risk-scan` job 状态 = **success**（exit 0）
- `medium-risk-scan` job 状态 = **success** 或 warning（取决于是否有 MEDIUM）
- 下载 artifact `kwarg-docker-high-risk-report`，确认 `kwarg-high-risk-report.json` 的 `summary.HIGH = 0`

**步骤 3**：日志关键字校验：
- `docker-scan-high.log` 含 `scan_complete` + `result=success` + `high_risk_count=0`
- 不含 `PermissionError` / `high_risk_detected`

### 4.3 回归验证（确保真实 HIGH 仍能阻断）

**步骤 1**：临时构造一个 HIGH 风险样本（在测试分支）：

```python
# tests/fixtures/kwarg_high_risk_sample.py
def bad_caller(**kwargs):
    # 故意触发 HIGH:在 **kwargs 展开前未过滤保留键
    requests.get(**kwargs)  # kwargs 可能含 headers/stream 等
```

**步骤 2**：本地跑扫描器指向该 fixture：
```powershell
docker run --rm -v "${PWD}:/project:ro" -v "${PWD}/scan-output:/output" `
  -e MIN_RISK=HIGH -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/output/report.json `
  kwarg-scanner:fix-test --path /project/tests/fixtures
echo "exit_code=$LASTEXITCODE"
# 期望:exit 1,report summary.HIGH >= 1
```

**步骤 3**：确认通过后删除 fixture 分支。

---

## 五、回滚方案

若修复引入新问题，按以下顺序回滚：

1. **快速回滚**：`git revert <修复 commit>`，恢复 yml + entrypoint 原状
2. **临时禁用 workflow**：在 kwarg-docker-scan.yml 顶部加 `if: false`（仅禁用触发，保留文件）
3. **保留修复、降级使用**：若 entrypoint 校验逻辑有问题，可仅回滚修改点 2，保留修改点 1（输出路径迁移本身无副作用）

回滚后 CI 恢复「误报阻断」状态，需尽快重新修复。

---

## 六、风险评估

| 风险点 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| `chmod 777 scan-output` 在某些 runner 上被策略限制 | 低 | 扫描失败 | 步骤 4.1 本地预演；CI 失败时改用 `docker create` + `docker cp` 方案 |
| entrypoint 的 Python 解析 JSON 失败（报告格式变更） | 低 | HIGH 计数恒为 0，真实 HIGH 漏报 | 修改点 3（P2）追加 findings 数组长度校验；扫描器单测覆盖报告 schema |
| `:ro` 只读挂载导致扫描器尝试写 cache 失败 | 低 | 扫描异常 | 本地验证步骤 3 会暴露；必要时改回读写挂载但输出仍走 /output |
| medium-risk-scan 的 `|| true` 吞掉真实失败 | 中 | MEDIUM 风险静默丢失 | 不在本次范围；建议后续移除 `|| true` 改为 `continue-on-error: true` |

---

## 七、执行清单

- [ ] 评审本方案（确认修改点 1/2 的 before/after diff）
- [ ] 执行修改点 1：kwarg-docker-scan.yml（high-risk-scan + medium-risk-scan）
- [ ] 执行修改点 2：docker-entrypoint.sh（exit 1 加证据校验）
- [ ] 本地验证 4.1 步骤 1-5（5 步全绿）
- [ ] 推送到 develop 触发 CI，验证 4.2 步骤 1-3
- [ ] 回归验证 4.3（真实 HIGH 仍阻断）
- [ ] 更新 `todo_followup_20260804.md` §1 状态为「已修复」
- [ ] 更新 `bom_fix_links_cleanup_summary_20260803.md` §四 CI 状态快照表

---

## 八、附录：修改文件清单

| 文件 | 修改类型 | 行数（约） |
|------|----------|-----------|
| [.github/workflows/kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml) | 修改 high-risk-scan + medium-risk-scan 两处 run 块 | +20 / -8 |
| [packages/kwarg_scanner/docker-entrypoint.sh](../../packages/kwarg_scanner/docker-entrypoint.sh) | 修改 `case 1)` 分支，加报告校验 | +25 / -8 |
