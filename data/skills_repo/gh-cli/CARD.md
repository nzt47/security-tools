# gh-cli

> LLM 整理，逐条经**原文引用校验**：verified 40 / 40（未通过的已丢弃）。

## 何时使用

- 需要从命令行完成 GitHub 仓库、问题、拉取请求、Actions、项目、发布、代码片段、代码空间、组织、扩展等各类 GitHub 操作时使用。

## 步骤

- macOS 上可用 Homebrew 安装 gh。
- Linux 上通过添加 GitHub CLI 软件源后使用 apt 安装 gh。
- Windows 上可用 winget 安装 GitHub CLI。
- 安装后运行 gh --version 验证安装。
- 执行交互式登录，默认针对 github.com。
- 可指定主机名登录（如企业实例）。
- 可从文件读取 token 完成登录。
- 用 gh auth status 检查认证状态。
- 可在不同账号间切换。
- 可登出指定主机与用户。
- 配置 git 使用 gh 作为凭据助手。
- 可查看当前生效的 token。
- 可刷新认证并追加权限范围。
- 用 gh config list 列出全部配置。
- 用 gh config get 读取指定配置项。
- 用 gh config set 修改配置项，如编辑器、git 协议、提示与分页器。
- 可清除配置缓存。
- 用 gh browse 在浏览器打开仓库，也可打开指定路径、issue/PR、提交或特定页面。
- 用 gh repo create 创建仓库，可指定描述、可见性、主页、许可证、gitignore、模板等。
- 用 gh repo clone 克隆仓库，可指定目录与分支。
- 用 gh repo list 列出仓库，可限制数量、过滤公开/源仓库、输出 JSON 并用 jq 过滤。
- 用 gh repo view 查看仓库详情，可输出 JSON 或在浏览器打开。
- 用 gh repo edit 修改描述、主页、可见性、功能开关、默认分支等。
- 用 gh repo rename 重命名仓库，用 gh repo archive/unarchive 归档或取消归档。
- 用 gh repo delete 删除仓库，可用 --yes 跳过确认。
- 用 gh repo fork 复刻仓库，可指定组织、复刻后克隆、设置远端名。
- 用 gh repo sync 同步复刻仓库，可指定分支或强制同步。
- 用 gh repo set-default 设置当前目录的默认仓库，可用 --unset 取消。
- 用 gh repo autolink 管理仓库自动链接，如 list 与 add。

## 参数

- 可通过环境变量 GH_TOKEN 提供 token 用于自动化。
- 可用 GH_HOST 指定 GitHub 主机名。
- 可用 GH_PROMPT_DISABLED 禁用交互提示。
- 可用 GH_EDITOR 指定编辑器，GH_PAGER 指定分页器。
- 可用 GH_TIMEOUT 设置 HTTP 超时。
- 可用 GH_REPO 覆盖默认仓库。
- 登录支持 --web 网页认证、--clipboard 复制 OAuth 码、--git-protocol 指定协议、--insecure-storage 明文存储。
- gh auth status 支持 --active、--hostname、--show-token、--json 与 --jq 过滤。
- gh auth refresh 支持 --remove-scopes 移除范围、--reset-scopes 重置为默认范围、--clipboard。

## 约束 / 反例

- 使用 gh auth login --insecure-storage 会以明文方式存储凭据。

## 示例

（原文没有这一节，或没有能通过引用校验的条目）
