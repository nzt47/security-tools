# release-docs.yml Artifact 名称修复方案

## 问题

`.github/workflows/release-docs.yml` 中 artifact 名称使用 `${{ github.ref_name }}`（两处）：

- `build-docs` job 第 137 行：`name: api-docs-${{ github.ref_name }}`（upload-artifact）
- `deploy-pages` job 第 158 行：`name: api-docs-${{ github.ref_name }}`（download-artifact）

**触发场景分析**：

| 触发事件 | `github.ref_name` | 风险 |
|---|---|---|
| `release published` | tag 名（如 `v1.2.0`） | ✅ 无 `/` |
| `push v* tag` | tag 名 | ✅ 无 `/` |
| `workflow_dispatch`（手动调试） | **分支名（如 `fix/ci-scan-optimization`）** | ❌ 含 `/` → `InvalidArtifactName`（NTFS 兼容限制，upload-artifact 报错） |

问题仅出现在 workflow_dispatch 手动触发且分支名含 `/` 时，release/tag 场景正常。

## 修复方案（与 Gitleaks CHG-2026-0801b 模式一致）

在 `build-docs` job 增加「安全 artifact 名预处理」步骤并输出到 job outputs，`deploy-pages` 通过 `needs.build-docs.outputs` 引用同一名称（保持两 job 名称一致，避免下载失败）。

### 1. `build-docs` job 增加 outputs（第 65 行后）

```yaml
  build-docs:
    name: 构建 API 文档
    needs: readiness-check
    runs-on: ubuntu-latest
    outputs:
      artifact_name: ${{ steps.artifact_name.outputs.name }}   # 供 deploy-pages 下载同名 artifact
    steps:
```

### 2. `build-docs` 首个步骤增加 SAFE 名称计算

```yaml
      - name: 准备安全的 artifact 名称
        id: artifact_name
        # 【变易】github.ref_name 在 workflow_dispatch 下为分支名（可能含 '/'），
        # 含 '/' 的 artifact 名非法（InvalidArtifactName，NTFS 兼容限制）。
        # 统一将 '/' 替换为 '-'，输出到 GITHUB_OUTPUT 供上传/下载步骤复用。
        # release/tag 触发（ref_name 为 tag 名）无 '/'，SAFE 与原始值一致，行为不变。
        run: |
          RAW="${{ github.ref_name }}"
          SAFE="${RAW//\//-}"
          echo "name=api-docs-${SAFE}" >> "$GITHUB_OUTPUT"
          echo "已计算 artifact 名称: api-docs-${SAFE} (原始 ref_name: ${RAW})"

      - name: 检出代码
```

### 3. 上传步骤使用安全名称（第 133-139 行）

```yaml
      - name: 上传文档 artifact
        # Why: 方便下载查看，也供 deploy-pages job 使用
        uses: actions/upload-artifact@v7
        with:
          name: ${{ steps.artifact_name.outputs.name }}    # 原: api-docs-${{ github.ref_name }}
          path: docs_build/
          retention-days: 30
```

### 4. `deploy-pages` 下载步骤使用同一安全名称（第 155-159 行）

```yaml
      - name: 下载文档 artifact
        uses: actions/download-artifact@v8
        with:
          name: ${{ needs.build-docs.outputs.artifact_name }}    # 原: api-docs-${{ github.ref_name }}
          path: docs_site/
```

## 验证建议

1. workflow_dispatch 在 `fix/xxx` 分支触发 release-docs → build-docs 上传成功，artifact 名为 `api-docs-fix-xxx`。
2. release/tag 触发 → artifact 名 `api-docs-v1.2.0`（SAFE 无变化，行为一致）。
3. deploy-pages 正常下载同名字 artifact。

## 备选方案（更简单，不推荐）

直接用 `${{ github.run_id }}` 作为 artifact 名（唯一且无非法字符），但丢失分支语义且需同步改 deploy-pages。推荐上述 SAFE 预处理（保留语义、与已有修复模式一致）。
