# 云枢测试环境一键部署 — Ansible Playbook

> **输入**: [docs/reviews/deployment_checklist.md](../../docs/reviews/deployment_checklist.md)（生产部署清单）
> **目标**: 把"配置校验重构"清单整理为可执行 Playbook，一键部署到测试环境

---

## 1. 目录结构

```
deploy/ansible/
├── README.md                          # 本文件
├── inventory.ini                      # 测试环境主机清单
├── group_vars/
│   └── test/
│       ├── vars.yml                   # 非敏感变量（端口/路径/开关）
│       └── vault.yml.example          # 敏感变量模板（复制为 vault.yml 后加密）
├── templates/
│   ├── env.j2                         # .env 渲染模板（密钥来自 Vault）
│   ├── app-server.service.j2          # 主应用 systemd 单元
│   ├── metrics-exporter.service.j2    # 缓存指标采集器 systemd 单元
│   └── prometheus-scrape.j2           # Prometheus 抓取配置片段
└── site.yml                           # 主 playbook（5 个 block + tags）
```

---

## 2. 前置准备

### 2.1 控制机（执行 ansible-playbook 的机器）

```bash
# 安装 ansible-core（≥ 2.12, 支持 ansible.builtin.* 完全限定名）
pip install ansible-core

# 或系统包管理器
# Ubuntu/Debian: apt install ansible
# macOS: brew install ansible
```

### 2.2 目标机（测试服务器）

| 要求 | 说明 |
|------|------|
| OS | Linux（systemd） |
| Python | ≥ 3.12（`python3 --version`） |
| SSH | 推荐免密登录（`ssh-copy-id deploy@<host>`） |
| sudo | `deploy` 账号需有免密 sudo 或对应权限 |
| Git | 已安装，部署 key 对 `git_repo` 有读权限 |

### 2.3 配置主机清单

编辑 [inventory.ini](inventory.ini)，把 `ansible_host` / `ansible_user` 替换为真实值：

```ini
[test]
yunshu-test-01 ansible_host=10.0.0.10 ansible_user=deploy
```

### 2.4 加密敏感变量（**必做**）

```bash
cd deploy/ansible/group_vars/test

# 1. 复制模板
cp vault.yml.example vault.yml

# 2. 编辑填入真实密钥（LLM_API_KEY / 数据库密码 / SECRET_KEY 等）
vim vault.yml

# 3. 加密（设置强 Vault 密码）
ansible-vault encrypt vault.yml

# 4. 验证加密成功（应显示 $ANSIBLE_VAULT）
head -1 vault.yml
```

> **【不易】安全约束**: `vault.yml` 必须加密提交；明文密钥禁止进版本库。`.gitignore` 应包含 `vault.yml`（非 `.example`）。

---

## 3. 一键部署

```bash
cd deploy/ansible

# 全量部署（preflight → deploy → monitoring → verify）
ansible-playbook -i inventory.ini site.yml --ask-vault-pass
```

执行流程（对应生产清单 §4）：

| Block | 对应清单章节 | 动作 |
|-------|-------------|------|
| preflight | §3 前置检查 | Python 版本 / git / 磁盘空间 |
| deploy | §4.1 标准部署 | 建用户 → git pull → venv → pip → 渲染 .env → 校验 config.yaml → systemd |
| monitoring | §6 监控指标 | 启动 metrics-exporter（9101）+ 注入 Prometheus scrape |
| verify | §4.2 功能验证 | import 校验 + validate_search_instance 5 用例 + /metrics + 9101 + 篡改降级 |

---

## 4. 分阶段执行（tags）

```bash
# 仅前置检查
ansible-playbook -i inventory.ini site.yml --tags preflight --ask-vault-pass

# 仅部署代码（不验证）
ansible-playbook -i inventory.ini site.yml --tags deploy --ask-vault-pass

# 仅接入监控
ansible-playbook -i inventory.ini site.yml --tags monitoring --ask-vault-pass

# 仅运行验证（部署后健康检查）
ansible-playbook -i inventory.ini site.yml --tags verify --ask-vault-pass
```

---

## 5. 回滚（对应清单 §5.1）

```bash
# 回滚到部署前的 commit（自动记录在 pre_deploy_commit 事实）
ansible-playbook -i inventory.ini site.yml \
  --tags rollback \
  -e rollback_to=HEAD~1 \
  --ask-vault-pass

# 回滚到指定 commit
ansible-playbook -i inventory.ini site.yml \
  --tags rollback \
  -e rollback_to=<commit-sha> \
  --ask-vault-pass

# 紧急回滚（git revert, 对应清单 §5.1）
ansible-playbook -i inventory.ini site.yml \
  --tags rollback \
  -e rollback_to=e6ed6b00~1 \
  --ask-vault-pass
```

> 回滚会 `git reset --hard` + 重装依赖 + 重启服务，不删除数据。

---

## 6. 变量说明

### 6.1 非敏感变量（[vars.yml](group_vars/test/vars.yml)）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `deploy_path` | `/opt/yunshu` | 代码部署根目录 |
| `app_port` | `5678` | 主应用端口（app_server.py） |
| `metrics_exporter_port` | `9101` | 缓存指标采集端口 |
| `log_level` | `INFO` | 全局日志级别 |
| `agent_hybrid_embedding` | `1` | 子进程隔离（守 0xC0000005 崩溃，**勿关**） |
| `deploy_prometheus_scrape` | `false` | 是否注入 Prometheus 抓取配置 |

### 6.2 敏感变量（[vault.yml.example](group_vars/test/vault.yml.example) → vault.yml）

| 变量 | 用途 |
|------|------|
| `llm_api_key` | LLM 服务密钥（**必填**，缺失则部署终止） |
| `llm_provider` / `llm_model` / `llm_base_url` | LLM 服务配置 |
| `glitchtip_admin_password` | GlitchTip 管理员密码 |
| `postgres_password` / `secret_key` | 数据库 / Django 密钥 |
| `search_*_api_key` | 搜索引擎 API Keys |

---

## 7. 部署后验证清单

`--tags verify` 会自动执行以下检查（对应生产清单 §4.2 + §6）：

- [x] `agent.config_validation` 模块可导入
- [x] `validate_search_instance` 5 个边界用例全部 PASS
- [x] HTTP `/metrics` 端点返回 200
- [x] 缓存指标 exporter 端口 9101 可达
- [x] `verify_config_tamper.py` 6 种篡改场景全部降级不抛异常

手动补充验证：

```bash
# 查看服务状态
ssh deploy@<host> 'systemctl status yunshu-app yunshu-config-metrics'

# 查看实时日志
ssh deploy@<host> 'journalctl -u yunshu-app -f'

# 手动触发缓存指标
curl http://127.0.0.1:9101/
curl http://127.0.0.1:5678/metrics | grep yunshu_config
```

---

## 8. 三义原则校验

- **【不易】** 安全边界不弱化：
  - `.env` 文件权限 `0600`，密钥经 Ansible Vault 加密，`no_log: true` 防日志泄露
  - systemd 单元 `NoNewPrivileges` + `ProtectSystem` + 非 root 运行
  - 降级链路保留（`AGENT_HYBRID_EMBEDDING=1`、`verify_config_tamper.py` 验证篡改降级）
  - Vault 变量缺失时 `assert` 直接终止，避免部署半成品

- **【变易】** 参数化与幂等：
  - `group_vars/test/` 按环境隔离，复制为 `staging/prod` 即换环境
  - `git` / `pip` / `template` 全幂等，多次执行无副作用
  - `tags` 分阶段控制，支持增量部署与独立验证

- **【简易】** 最小充分解：
  - 单 `site.yml` + 5 个 block，不引入 roles 过度抽象
  - 模板外置避免 playbook 膨胀
  - 一条命令完成全量部署

---

## 9. 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `Vault 变量校验通过` 失败 | `vault.yml` 未加密或 `llm_api_key` 仍为占位符 | 按 §2.4 加密 |
| `git clone` 权限拒绝 | 部署 key 未配置 | 目标机 `deploy` 用户加 SSH key 到 GitHub |
| `pip install` 超时 | 网络问题 | `vars.yml` 加 `pip_index_url` 并在 pip 模块配置 |
| systemd 启动失败 | `.env` 缺失或语法错误 | `journalctl -u yunshu-app` 查日志 |
| `/metrics` 404 | `prometheus_flask_exporter` 未装 | 检查 `requirements.txt` 是否含该依赖 |
| 9101 端口不通 | metrics-exporter 未启动 | `systemctl status yunshu-config-metrics` |

---

## 10. 关联文档

- [配置校验重构部署清单](../../docs/reviews/deployment_checklist.md) — 本 Playbook 的输入源
- [tlm-ops-reporter 生产部署指南](../../docs/ops_daily/production_deployment_guide.md) — K8s/Helm 部署（与本 Playbook 互补）
- [config_metrics_exporter.py](../../scripts/config_metrics_exporter.py) — 缓存指标采集脚本
- [verify_config_tamper.py](../../scripts/verify_config_tamper.py) — 篡改降级验证脚本
