#!/bin/bash
# 发布脚本: 等 CI 测试通过后自动打 tag 并推送到 release 分支
#
# 用法:
#   bash scripts/dev/release.sh check              # 检查 CI 状态
#   bash scripts/dev/release.sh tag v1.3.0         # 打指定版本 tag
#   bash scripts/dev/release.sh push-release       # 推送到 release 分支
#   bash scripts/dev/release.sh release v1.3.0     # 一键发布 (检查CI + 打tag + 推送release)
#
# 前置条件:
#   - gh CLI 已安装并认证
#   - SSH 配置已就绪 (fix_git_network.sh)
#   - 当前在 master 分支且工作区干净

set -euo pipefail

# ── 颜色输出 ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
info()  { echo -e "      $1"; }

# ── 配置 ────────────────────────────────────────────────────────────
RELEASE_BRANCH="release"
MAIN_BRANCH="master"
# 用文件名而非显示名: ci.yml 与 test.yml 同名"云枢系统测试流程", 按名称查找会二义报错
WORKFLOW_NAME="ci.yml"

# ── 检查前置条件 ────────────────────────────────────────────────────
check_prerequisites() {
    echo "=========================================="
    echo "  检查前置条件"
    echo "=========================================="

    # 1. 检查 gh CLI
    if ! command -v gh &>/dev/null; then
        fail "gh CLI 未安装"
        info "安装: https://cli.github.com/"
        return 1
    fi
    ok "gh CLI 已安装"

    # 2. 检查 gh 认证
    if ! gh auth status &>/dev/null; then
        fail "gh CLI 未认证"
        info "运行: gh auth login"
        return 1
    fi
    ok "gh CLI 已认证"

    # 3. 检查当前分支
    local current_branch
    current_branch=$(git branch --show-current)
    if [ "$current_branch" != "$MAIN_BRANCH" ]; then
        warn "当前分支: $current_branch (建议在 $MAIN_BRANCH 分支发布)"
    else
        ok "当前在 $MAIN_BRANCH 分支"
    fi

    # 4. 检查工作区是否干净
    if [ -n "$(git status --porcelain)" ]; then
        warn "工作区有未提交变更 (建议先提交)"
    else
        ok "工作区干净"
    fi
}

# ── 检查 CI 状态 ────────────────────────────────────────────────────
check_ci() {
    echo "=========================================="
    echo "  检查 CI 状态"
    echo "=========================================="

    # 获取最新的 CI 运行
    local run_id
    run_id=$(gh run list --workflow "$WORKFLOW_NAME" --branch "$MAIN_BRANCH" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")

    if [ -z "$run_id" ]; then
        fail "未找到 CI 运行记录"
        info "可能是 workflow 名称不匹配: $WORKFLOW_NAME"
        return 1
    fi

    info "最新 CI 运行 ID: $run_id"

    # 获取运行状态
    local status conclusion
    status=$(gh run view "$run_id" --json status --jq '.status' 2>/dev/null || echo "unknown")
    conclusion=$(gh run view "$run_id" --json conclusion --jq '.conclusion' 2>/dev/null || echo "null")

    echo ""
    echo "=== CI 运行详情 ==="
    gh run view "$run_id" --json status,conclusion,displayTitle,createdAt --jq '{status: .status, conclusion: .conclusion, title: .displayTitle, created: .createdAt}' 2>/dev/null || true
    echo ""

    case "$status" in
        completed)
            if [ "$conclusion" = "success" ]; then
                ok "CI 测试通过 ✅"
                return 0
            else
                fail "CI 测试失败: $conclusion"
                info "查看详情: gh run view $run_id"
                return 1
            fi
            ;;
        in_progress|queued)
            warn "CI 正在运行中: $status"
            info "等待完成: gh run watch $run_id"
            info "或稍后重试: bash $0 check"
            return 1
            ;;
        *)
            fail "CI 状态未知: $status"
            return 1
            ;;
    esac
}

# ── 打 tag ──────────────────────────────────────────────────────────
create_tag() {
    local version="$1"

    echo "=========================================="
    echo "  打 Tag: $version"
    echo "=========================================="

    # 验证版本号格式
    if ! echo "$version" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
        fail "版本号格式错误: $version"
        info "正确格式: v1.0.0 (v主版本.次版本.修订号)"
        return 1
    fi

    # 检查 tag 是否已存在
    if git rev-parse "$version" &>/dev/null; then
        fail "Tag 已存在: $version"
        info "删除旧 tag: git tag -d $version"
        return 1
    fi

    # 获取最新提交信息
    local commit_msg
    commit_msg=$(git log -1 --pretty=format:"%s")

    # 打 annotated tag
    git tag -a "$version" -m "Release $version

基于提交: $commit_msg

发布日期: $(date '+%Y-%m-%d')
发布者: $(git config user.name) <$(git config user.email)>"

    ok "Tag 已创建: $version"
    info "提交: $commit_msg"

    # 推送 tag
    echo ""
    echo "=== 推送 Tag ==="
    git push origin "$version"
    ok "Tag 已推送到远程: $version"
}

# ── 推送到 release 分支 ─────────────────────────────────────────────
push_release() {
    echo "=========================================="
    echo "  推送到 $RELEASE_BRANCH 分支"
    echo "=========================================="

    # 检查 release 分支是否存在
    if git ls-remote --heads origin "$RELEASE_BRANCH" | grep -q "$RELEASE_BRANCH"; then
        info "$RELEASE_BRANCH 分支已存在于远程"
        # 更新 release 分支
        git push origin "$MAIN_BRANCH:$RELEASE_BRANCH" --force-with-lease
        ok "已更新 $RELEASE_BRANCH 分支 (force-with-lease)"
    else
        info "$RELEASE_BRANCH 分支不存在, 创建新分支"
        git push origin "$MAIN_BRANCH:refs/heads/$RELEASE_BRANCH"
        ok "已创建 $RELEASE_BRANCH 分支"
    fi
}

# ── 创建 GitHub Release ─────────────────────────────────────────────
create_github_release() {
    local version="$1"

    echo "=========================================="
    echo "  创建 GitHub Release: $version"
    echo "=========================================="

    # 检查 CHANGELOG 中是否有对应版本
    local changelog_section
    changelog_section=""

    # 生成 release notes
    local release_notes
    release_notes=$(git log "$version" --pretty=format:"* %s" --no-merges -20 2>/dev/null || echo "Release $version")

    # 创建 GitHub Release
    gh release create "$version" \
        --title "Release $version" \
        --notes "$(cat <<EOF
## 变更内容

$release_notes

## 发布信息

- **版本**: $version
- **发布日期**: $(date '+%Y-%m-%d')
- **基于分支**: $MAIN_BRANCH

---
详细变更请查看 [CHANGELOG.md](CHANGELOG.md)
EOF
)" \
        --target "$MAIN_BRANCH" 2>&1 || warn "GitHub Release 创建失败 (可能已存在)"

    ok "GitHub Release 已创建: $version"
}

# ── 一键发布 ────────────────────────────────────────────────────────
full_release() {
    local version="$1"

    echo "=========================================="
    echo "  一键发布: $version"
    echo "=========================================="
    echo ""

    # 1. 检查前置条件
    check_prerequisites || return 1
    echo ""

    # 2. 检查 CI 状态
    check_ci || return 1
    echo ""

    # 3. 打 tag
    create_tag "$version" || return 1
    echo ""

    # 4. 推送到 release 分支
    push_release || return 1
    echo ""

    # 5. 创建 GitHub Release
    create_github_release "$version" || true
    echo ""

    echo "=========================================="
    echo "  发布完成: $version ✅"
    echo "=========================================="
    info "Tag: $version"
    info "Release 分支: $RELEASE_BRANCH"
    info "GitHub Release: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/releases/tag/$version"
}

# ── 主逻辑 ──────────────────────────────────────────────────────────
main() {
    local cmd="${1:-help}"

    case "$cmd" in
        check)
            check_prerequisites
            echo ""
            check_ci
            ;;
        tag)
            if [ -z "${2:-}" ]; then
                fail "请指定版本号: bash $0 tag v1.0.0"
                exit 1
            fi
            check_prerequisites
            create_tag "$2"
            ;;
        push-release)
            check_prerequisites
            push_release
            ;;
        release)
            if [ -z "${2:-}" ]; then
                fail "请指定版本号: bash $0 release v1.0.0"
                exit 1
            fi
            full_release "$2"
            ;;
        help|--help|-h|"")
            echo "发布脚本: 等 CI 测试通过后自动打 tag 并推送到 release 分支"
            echo ""
            echo "用法:"
            echo "  bash $0 check              检查 CI 状态"
            echo "  bash $0 tag v1.0.0         打指定版本 tag"
            echo "  bash $0 push-release       推送到 release 分支"
            echo "  bash $0 release v1.0.0     一键发布 (检查CI + 打tag + 推送release + GitHub Release)"
            echo ""
            echo "前置条件:"
            echo "  - gh CLI 已安装并认证"
            echo "  - SSH 配置已就绪"
            echo "  - 当前在 master 分支"
            ;;
        *)
            fail "未知命令: $cmd"
            echo "运行 'bash $0 help' 查看用法"
            exit 1
            ;;
    esac
}

main "$@"
