# SSH 配置指南 - 使用 SSH 协议推送代码到 GitHub/Gitee

本指南详细说明如何在 Windows 环境下配置 SSH 密钥，并通过 SSH 协议
推送代码到 GitHub 或 Gitee 远程仓库。

---

## 目录

1. [前置条件检查](#1-前置条件检查)
2. [生成 SSH 密钥对](#2-生成-ssh-密钥对)
3. [添加密钥到 ssh-agent](#3-添加密钥到-ssh-agent)
4. [配置 GitHub SSH](#4-配置-github-ssh)
5. [配置 Gitee SSH](#5-配置-gitee-ssh)
6. [测试 SSH 连接](#6-测试-ssh-连接)
7. [切换远程地址为 SSH](#7-切换远程地址为-ssh)
8. [推送代码](#8-推送代码)
9. [多账号配置（可选）](#9-多账号配置可选)
10. [常见问题排查](#10-常见问题排查)

---

## 1. 前置条件检查

### 1.1 检查 Git 是否已安装

```powershell
git --version
# 预期输出: git version 2.x.x.windows.x
```

### 1.2 检查 OpenSSH 客户端

```powershell
# Windows 10/11 通常自带 OpenSSH 客户端
ssh -V
# 预期输出: OpenSSH_for_Windows_8.x
```

如果没有安装，前往 `设置 → 应用 → 可选功能 → 添加功能 → OpenSSH 客户端`。

### 1.3 检查现有 SSH 密钥

```powershell
# 检查 ~/.ssh 目录是否已有密钥
if (Test-Path "$env:USERPROFILE\.ssh") {
    Get-ChildItem "$env:USERPROFILE\.ssh" -Filter "id_*"
} else {
    Write-Output "尚无 .ssh 目录，需要创建"
}
```

如果已有 `id_ed25519` 和 `id_ed25519.pub`，可以跳到第 4 步，或按需生成新密钥。

---

## 2. 生成 SSH 密钥对

### 2.1 生成 Ed25519 密钥（推荐）

```powershell
ssh-keygen -t ed25519 -C "13539371839@139.com"
```

> **说明**：
> - `-t ed25519`: 使用 Ed25519 算法（比 RSA 更安全、更快）
> - `-C "邮箱"`: 添加注释标签（使用你的 git config 邮箱）

### 2.2 交互式提示处理

系统会依次询问以下内容，建议按如下方式回答：

```
Enter file in which to save the key (~/.ssh/id_ed25519):
# 直接按回车，使用默认路径

Enter passphrase (empty for no passphrase):
# 输入一个密码（可选，增加安全性）或直接回车留空

Enter same passphrase again:
# 再次输入相同的密码
```

### 2.3 生成 RSA 密钥（兼容旧系统）

如果目标平台不支持 Ed25519，使用 RSA：

```powershell
ssh-keygen -t rsa -b 4096 -C "13539371839@139.com"
```

### 2.4 验证密钥生成

```powershell
Get-ChildItem "$env:USERPROFILE\.ssh"
# 预期看到:
#   id_ed25519       (私钥 - 绝对不能泄露)
#   id_ed25519.pub   (公钥 - 上传到 GitHub/Gitee)
```

---

## 3. 添加密钥到 ssh-agent

### 3.1 启动 ssh-agent 服务

```powershell
# 以管理员身份运行 PowerShell
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent
```

### 3.2 添加密钥

```powershell
ssh-add "$env:USERPROFILE\.ssh\id_ed25519"
# 预期输出: Identity added: ...
```

### 3.3 验证已添加的密钥

```powershell
ssh-add -l
# 预期输出: 256 SHA256:xxxxxx ... (ED25519)
```

---

## 4. 配置 GitHub SSH

### 4.1 复制公钥内容

```powershell
# 将公钥内容复制到剪贴板
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | Set-Clipboard
# 或使用 clip 命令
cat "$env:USERPROFILE\.ssh\id_ed25519.pub" | clip
```

### 4.2 添加到 GitHub

1. 登录 [GitHub](https://github.com)
2. 点击右上角头像 → **Settings**
3. 左侧菜单 → **SSH and GPG keys**
4. 点击 **New SSH key**
5. 填写信息：
   - **Title**: `Windows-Agent-PC`（自定义名称）
   - **Key type**: `Authentication Key`
   - **Key**: 粘贴剪贴板内容（`Ctrl+V`）
6. 点击 **Add SSH key**

### 4.3 验证 GitHub SSH 连接

```powershell
ssh -T git@github.com
# 首次连接会提示确认指纹，输入 yes
# 预期输出: Hi nzt47! You've successfully authenticated, but GitHub does not provide shell access.
```

---

## 5. 配置 Gitee SSH

### 5.1 复制公钥内容

```powershell
# 使用与 GitHub 相同的公钥，或生成独立密钥
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | Set-Clipboard
```

### 5.2 添加到 Gitee

1. 登录 [Gitee](https://gitee.com)
2. 点击右上角头像 → **设置**
3. 左侧菜单 → **SSH公钥**
4. 填写信息：
   - **标题**: `Windows-Agent-PC`
   - **公钥**: 粘贴剪贴板内容
5. 点击 **确定**

### 5.3 验证 Gitee SSH 连接

```powershell
ssh -T git@gitee.com
# 首次连接会提示确认指纹，输入 yes
# 预期输出: Hi nzt47! You've successfully authenticated, but GITEE.COM does not provide shell access.
```

---

## 6. 测试 SSH 连接

### 6.1 测试 GitHub

```powershell
ssh -T git@github.com
```

### 6.2 测试 Gitee

```powershell
ssh -T git@gitee.com
```

### 6.3 详细调试模式

如果连接失败，使用 `-v` 参数查看详细日志：

```powershell
ssh -vT git@github.com
ssh -vT git@gitee.com
```

---

## 7. 切换远程地址为 SSH

### 7.1 查看当前远程地址

```powershell
cd C:\Users\Administrator\agent
git remote -v
```

### 7.2 切换到 GitHub SSH

```powershell
git remote set-url origin git@github.com:nzt47/security-tools.git
```

### 7.3 切换到 Gitee SSH

```powershell
git remote set-url origin git@gitee.com:nzt47/security-tools.git
```

### 7.4 验证切换结果

```powershell
git remote -v
# 预期输出:
# origin  git@github.com:nzt47/security-tools.git (fetch)
# origin  git@github.com:nzt47/security-tools.git (push)
```

---

## 8. 推送代码

### 8.1 首次推送（设置上游跟踪）

```powershell
git push -u origin master
```

### 8.2 后续推送

```powershell
git push origin master
```

### 8.3 推送所有分支和标签

```powershell
git push origin --all
git push origin --tags
```

---

## 9. 多账号配置（可选）

如果需要同时使用多个 GitHub/Gitee 账号，使用 SSH config 文件区分。

### 9.1 为不同账号生成不同密钥

```powershell
# 个人账号
ssh-keygen -t ed25519 -C "personal@example.com" -f "$env:USERPROFILE\.ssh\id_ed25519_personal"

# 工作账号
ssh-keygen -t ed25519 -C "work@example.com" -f "$env:USERPROFILE\.ssh\id_ed25519_work"
```

### 9.2 创建 SSH config 文件

```powershell
# 创建或编辑 ~/.ssh/config 文件
notepad "$env:USERPROFILE\.ssh\config"
```

### 9.3 配置内容

```ssh-config
# GitHub 个人账号
Host github-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_personal
    IdentitiesOnly yes

# GitHub 工作账号
Host github-work
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_work
    IdentitiesOnly yes

# Gitee 账号
Host gitee
    HostName gitee.com
    User git
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

### 9.4 使用多账号远程地址

```powershell
# 个人账号仓库
git remote set-url origin git@github-personal:nzt47/security-tools.git

# 工作账号仓库
git remote set-url origin git@github-work:company/repo.git

# Gitee 仓库
git remote set-url origin git@gitee:nzt47/security-tools.git
```

---

## 10. 常见问题排查

### 问题 1: Permission denied (publickey)

**原因**：SSH 密钥未正确配置或未添加到 GitHub/Gitee。

**解决**：
```powershell
# 1. 检查密钥是否存在
Test-Path "$env:USERPROFILE\.ssh\id_ed25519"

# 2. 检查 ssh-agent 是否运行
Get-Service ssh-agent

# 3. 添加密钥到 agent
ssh-add "$env:USERPROFILE\.ssh\id_ed25519"

# 4. 验证连接
ssh -T git@github.com
```

### 问题 2: ssh-agent 服务未启动

**解决**：
```powershell
# 以管理员身份运行
Start-Service ssh-agent
# 或设置为自动启动
Get-Service ssh-agent | Set-Service -StartupType Automatic
```

### 问题 3: 端口 22 被防火墙阻止

**解决**：使用 GitHub 的 443 端口 SSH

编辑 `~/.ssh/config`：
```ssh-config
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519
```

### 问题 4: Could not resolve hostname

**原因**：DNS 解析失败或网络问题。

**解决**：
```powershell
# 检查 DNS 解析
nslookup github.com
nslookup gitee.com

# 如果 DNS 失败，尝试配置 hosts 文件
# 以管理员身份编辑 C:\Windows\System32\drivers\etc\hosts
# 添加:
#   140.82.112.3  github.com
#   212.64.63.190 gitee.com
```

### 问题 5: 密钥权限过于开放

**解决**：
```powershell
# Windows 上使用 icacls 重置权限
icacls "$env:USERPROFILE\.ssh\id_ed25519" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

### 问题 6: 推送时提示 "Updates were rejected"

**原因**：远程仓库有本地没有的提交。

**解决**：
```powershell
# 先拉取远程更改，再推送
git pull origin master --rebase
git push origin master
```

---

## 快速命令速查表

| 操作 | 命令 |
|------|------|
| 生成密钥 | `ssh-keygen -t ed25519 -C "邮箱"` |
| 查看公钥 | `cat ~/.ssh/id_ed25519.pub` |
| 复制公钥 | `cat ~/.ssh/id_ed25519.pub \| clip` |
| 启动 agent | `Start-Service ssh-agent` |
| 添加密钥 | `ssh-add ~/.ssh/id_ed25519` |
| 测试 GitHub | `ssh -T git@github.com` |
| 测试 Gitee | `ssh -T git@gitee.com` |
| 切换远程 | `git remote set-url origin git@github.com:user/repo.git` |
| 推送代码 | `git push -u origin master` |

---

## 当前项目配置状态

- **Git 用户名**: `nzt47`
- **Git 邮箱**: `13539371839@139.com`
- **当前远程地址**: `https://gitee.com/nzt47/security-tools.git` (HTTPS)
- **待提交 commits**: `4608614c` + `bc3e67f6` + `f44967c2` + `252307a0`

配置 SSH 后，执行以下命令完成推送：

```powershell
cd C:\Users\Administrator\agent
git remote set-url origin git@gitee.com:nzt47/security-tools.git
git push -u origin master
```
