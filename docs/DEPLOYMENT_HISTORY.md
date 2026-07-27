# 部署历史记录

> 本文档记录 GitHub Pages 部署的变更历史，用于验证自动部署 workflow 是否正常工作。

## 2026-07-28 初始部署

| 项目 | 值 |
|---|---|
| 部署方式 | GitHub Actions (workflow 模式) |
| Workflow 文件 | `.github/workflows/deploy-pages.yml` |
| 触发条件 | `docs/**` 路径变更 push 到 master |
| Pages URL | https://nzt47.github.io/security-tools/ |
| build_type | workflow |
| 首次 commit | `cc9ca741` ci(pages): 新增 docs/ 目录变更自动触发 GitHub Pages 部署 workflow |
| 首次构建耗时 | 43s（build 19s + deploy 11s） |

## 验证测试

### 2026-07-28 模拟变更测试

- **变更内容**: 新增本部署历史文档
- **预期行为**: push 后自动触发 deploy-pages workflow
- **验证方法**: 观察 workflow 执行状态 + 访问 Pages URL

---

> 每次重要部署后请在本文档追加记录。
