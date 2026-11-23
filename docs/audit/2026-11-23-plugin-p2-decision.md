# 云枢插件化 P2 剩余项：优先级与实施方案（2026-11-23）

> 状态：**Owner 已拍板（2026-11-23 13:16）——按推荐做**：① 签名先行；D1 Owner 离线私钥；
> D2 接受 AppContainer「默认无网络、无用户目录」；D3 Docker 仅作可选后端；
> D4 方案 + 校验器（后按「一次做完」改为接线，见 §3.5）。本文不把沙箱接生产、不批量补 schema。
> 依据：`docs/audit/2026-11-22-pluginization-risk-audit.md` §10 的「明确未做」清单，
> 交接条目 `plugin-security-p2-remaining`（open）。
> 实测时间：2026-11-23 12:11 交接卡（master `69c2bd7ee`，三处一致）+ 12:2x 本机探针。

## 0. 结论（先看这一页）

1. **当前不是现网风险，是「待启用」风险**：第三方插件包默认关（`CP_PLUGIN_PACKAGES_ENABLED=0`）、
   来源白名单为空、已装包 0 个。安装/启用入口现在都是 403，签名与沙箱缺失暂时打不到生产。
2. **五项按「解锁什么 / 成本 / 依赖」排序**：
   ① 签名与发布密钥（1–1.5 天，先方案后接线）→ ② OS 级文件/网络沙箱（AppContainer 3–5 天；
   Docker 只作可选后端）→ ③ HTTP 路由硬超时（5–10 天，架构级）→ ④ result_schema L2（24 个工具，1–2 天）
   → ⑤ 插件 UI iframe（继续冻结，当前只有 demo）。
3. **签名已完整落地**：校验器 + 离线签名工具 + 安装暂存/加载复验 + `CP_PLUGIN_SIGNATURE_MODE`；
   受信表 `config/plugin_trusted_keys.json` 为空（fail-closed），第三方包总开关仍默认关。
   插件相关 **102 passed**（签名 18 / 接线 8 / CLI 2）。
4. **AppContainer 已做可行性 PoC**：文件读、外网、环境变量三层都隔住；剩余是启动器工程化
   （3–5 天），**未接生产**。插件路由 p99 已实测（见 §5），当前没有持续慢路由。
5. **Owner 已拍板**（见 §7）：D1 Owner 离线私钥 / D2 接受 AppContainer 默认无网 / D3 Docker 可选 /
   D4 原先只做校验器，后按「全部做完」改为接线。

## 1. 现状实测（2026-11-23 12:2x）

| 项 | 读数 | 来源 / 备注 |
|---|---|---|
| 工具 | 101 个；critical 6 / high 15 / medium 28 / low 52 | `data/tool_definitions/*.yaml` |
| 有 `result_schema` | 24 / 101（critical 3、high 4、medium 4、low 13） | 缺：critical 3、high 11、medium 24、low 39 |
| 后端 HTTP 插件 | 14 个 / 226 条路由（全部内置，宿主进程内） | 审计 §3 / §4.1（2026-11-22） |
| 第三方插件包 | 0 个；安装/启用默认 403；白名单空 | `plugin_policy.py` + `/api/plugins` |
| 进程边界 | 子进程 + JSON-RPC + 超时硬杀 + 最小 env（默认开） | `plugin_process.py` / `plugin_worker.py` |
| 每插件并发 | bulkhead 默认 4，满即 429（非阻塞） | `app_server.py` 968–1010 |
| 宿主线程 | waitress `threads=16`，无每请求超时 | `app_server.py` 3022 |
| 本机 OS | Windows 10 专业版 19045；Python 3.12.0；pywin32 可用 | 探针 |
| 容器 | Docker CLI 29.4.3 已装，**daemon 未运行**；WSL Ubuntu 24.04 | `docker ps` 连不上 npipe |
| 加密库 | `cryptography==50.0.2`（requirements 已钉） | Ed25519 可直接用 |

**签名 PoC 结论（已在本机跑过，未提交）**：对 50 个文件做「文件树 → 规范化 JSON → Ed25519 分离签名」，
签名 22 ms、验签 14 ms；篡改文件、夹带额外文件、换公钥、伪造签名全部被拒。
说明方案在 Python 侧没有技术障碍，成本主要在密钥生命周期与安装流程改造。

## 2. 五项对比矩阵

| 项 | 解决什么 | 不解决什么 | 前置 | 工作量 | 风险 | 建议 |
|---|---|---|---|---|---|---|
| ① 签名/发布密钥 | 包内容与发布者绑定；篡改、夹带、错包可拒 | 已签名的恶意代码仍以用户权限运行；pip 依赖不在签名内 | Owner 定签名主体与私钥保管 | 方案 0.5 天；接线 + 测试 1 天 | 低（默认关时零行为变化） | **先做** |
| ② OS 级 FS/net 沙箱 | 读不到用户文件、默认无外网、资源可限 | 不解决供应链；不解决逻辑漏洞 | Windows AppContainer 进程启动器 | AppContainer 3–5 天；Docker 1–2 天但依赖 daemon | 中高（兼容性） | 先 1 天 PoC |
| ③ HTTP 路由硬超时 | 慢插件不吃满 waitress 线程 | 不解决越权/文件访问 | 路由出进程或可杀 worker | 5–10 天（226 条路由） | 高（改调用形态） | 先量 p99 再定 |
| ④ result_schema L2 | 输出契约、上游可校验 | 不解决安全 | 逐工具真实返回 | 24 × 10–20 分钟 + 用例 | 中（猜错=假契约） | 逐工具做 |
| ⑤ 插件 UI iframe | 插件 JS 不同源、令牌不可读 | 不解决后端权限 | 无 | 2–3 天 | 低 | 继续冻结 |

## 3. 方案①：签名与发布密钥

### 3.1 威胁模型

- 白名单里的 GitHub 仓库被投毒、分支被改写、维护者账号被盗；
- 本地 `local:` 路径被替换；下载链路被中间人替换；
- 包内夹带安装器没预期的文件（后门模块、启动脚本）。

签名只回答「这堆字节是不是我信任的发布者签的」，**不回答「这段代码运行起来安不安全」**。

### 3.2 信任模型与角色

| 角色 | 持有 | 做什么 | 不做什么 |
|---|---|---|---|
| Owner（根信任） | 离线私钥（加密介质） | 签发布包、批准公钥变更、批准撤销 | 不把私钥放服务器/CI/.env |
| 发布经理（可选） | 二级私钥 | 按 Owner 授权签包 | 不能改信任根 |
| CI | 只有公钥 | 验签、跑测试 | 永不持有私钥 |
| 服务器 | 只有公钥 | 安装/加载时验签 | 不签包、不生成密钥 |

**公钥分发**：仓库内 `config/plugin_trusted_keys.json` 固定，随代码 review 与部署走；
格式 `{schema:1, keys:[{key_id, alg:"ed25519", public_key_b64, status:"active|retired|revoked",
not_before, not_after, revoked_at, note}]}`。默认路径写死，**不**用环境变量指向任意文件
（否则改一个 env 就能换信任根）。

### 3.3 密钥生命周期

1. **生成**：离线机器上 `Ed25519PrivateKey.generate()`；私钥用口令加密后放加密 U 盘/离线介质，
   另一份密封备份；公钥进仓库。私钥**不进** git、不进 `.env`、不进服务器磁盘、不进 CI secret。
2. **轮换**：新 `key_id` 加入 `keys` 且 `status=active`；旧 key 改 `retired`（不再接受新签名，
   已安装包在过渡期内仍可验；过渡期结束改 `revoked`）。一次发布**只用一个** key，包里写 `key_id`。
3. **撤销**：`status=revoked` + `revoked_at`；验签时 `revoked` 一律拒绝（安装与加载都拒）。
   若私钥泄露：先把 key 置 revoked → 阻止新安装 → 审计已安装包（按 `package_sha256` 列清单）→
   重新签名/重新发布 → 通知使用者。**没有在线 OCSP**，撤销靠部署新配置，必须写进 runbook。
4. **有效期**：`not_after` 建议 12 个月；到期不自动失效已安装包，但拒绝新安装，逼轮换。

### 3.4 包格式与校验算法

分离签名文件 `plugin.sig.json` 放在包根（不参与自身摘要）：

```json
{
  "schema": 1,
  "key_id": "yunshu-release-2026",
  "signed_at": "2026-11-23T12:00:00Z",
  "package_sha256": "<对 files 规范化 JSON 的 sha256>",
  "files": {"plugin.json": "<sha256>", "pkg/__init__.py": "<sha256>"},
  "signature": "<base64(Ed25519(canonical(body))>"
}
```

校验顺序（全部通过才允许落盘/加载）：

1. 读 `plugin.sig.json`；缺 → `PLUGIN_SIGNATURE_MISSING`。
2. `key_id` 在受信表且 `active`（`retired` 只允许「已安装包的加载」，新安装拒绝；`revoked` 全拒）。
3. 用规范化 JSON（`sort_keys=True`、`separators=(",",":")`、UTF-8、无 BOM）重算 body 并验签。
4. 枚举包内文件（拒符号链接、拒 `..`、拒绝对路径、限文件数与总大小），逐文件 sha256；
   与 `files` **完全相等**（多一个文件也算失败）；再校验 `package_sha256`。
5. 校验通过后才从暂存目录移动到最终安装目录；失败即删暂存。

**加载时复验**：`load_plugin` / `_load_plugin_isolated` 对已安装目录重算文件树（50 文件约 14 ms），
防止安装后被替换。加载失败要写 manifest `status/load_error`，不能只 print。

### 3.5 安装器接线（2026-11-23 已落地）

三条安装路径统一改成「先暂存、验签、再落最终目录」：

```text
下载/复制 → plugins_dir/.staging/<uuid>/ → 验签 → shutil.move 到 plugins/<name>
          → 依赖装 .deps → 登记；任一步失败 _abort 清暂存 + 半装目录
```

- 接入点：`agent/extensions/plugins_installer.py` 的 `install_plugin`（下载/复制后、依赖前）
  与 `load_plugin`（加载前复验；`retired` 只允许加载，`revoked` 全拒）。
- 开关：`CP_PLUGIN_SIGNATURE_MODE=off|warn|enforce`，默认 **enforce**（非法值也 enforce）；
  已登记 `agent/settings/registry.py`（B 级，`_enum`）。
- 生成物：加载复验忽略 `.deps/`、`__pycache__/`、`*.pyc`、`.git/`；除此之外多/少文件都判篡改。
  `github:` 的 `_merge_dir` 不再复制 `.git`，避免「签名只覆盖源码树」被 VCS 元数据破坏。
- 受信表：`config/plugin_trusted_keys.json`（固定路径，不提供 env 改信任根）；当前 `keys: []`，
  即启用第三方包后**任何包都验不过**，这是有意的 fail-closed 初态。
- 错误码：`PLUGIN_SIGNATURE_MISSING / INVALID / UNKNOWN_KEY / KEY_REVOKED / KEY_RETIRED /
  KEY_EXPIRED / TAMPERED / PACKAGE_UNSAFE_PATH / PACKAGE_TOO_LARGE`；响应只回错误码与固定文案。
- 默认行为不变：`CP_PLUGIN_PACKAGES_ENABLED=0` 时安装/启用仍是 403，签名门不会先跑。

### 3.6 签名工具与发布流程

- `scripts/sign_plugin_package.py sign --package <dir> --key <pem> --key-id <id>`：离线运行，
  生成/覆盖 `plugin.sig.json`，输出 `public_key_b64` 供贴进受信表；
  `verify --package <dir> --keys config/plugin_trusted_keys.json` 供维护者/CI 复核。**不进 CI 自动签**。
  私钥口令用 `--password-env <VAR>` 传，不把口令放进命令行。
- 发布清单：干净构建 → 生成 sig → 用公钥复核 → 打 tag（`@ref` 固定到该 tag/commit）→
  更新白名单（若有）→ 记录 `package_sha256`。
- 私钥使用记录（谁、何时、签了哪个 sha256）写在发布记录里，不进代码库。

### 3.7 验收与测试

| 用例 | 期望 |
|---|---|
| 合法包 + 正确公钥 | 安装成功，文件树与签名一致 |
| 改一个字节 | `PLUGIN_SIGNATURE_TAMPERED` |
| 夹带额外文件 / 符号链接 / `../` | 拒绝 |
| 未知 key_id / revoked key | `UNKNOWN_KEY` / `KEY_REVOKED` |
| 缺 sig（enforce） | `SIGNATURE_MISSING`；warn 模式只记审计 |
| 安装后替换文件再加载 | 加载拒绝并写 `load_error` |
| 默认配置（包总开关关） | 仍 403 `PLUGIN_PACKAGES_DISABLED`，签名不改变现有行为 |

### 3.8 边界（必须如实写进 UI/文档）

- 签名 ≠ 沙箱：签名包仍以当前用户身份运行，能读用户文件、发网络请求。
- 签名不覆盖 `pip` 依赖（依赖装进 `.deps` 的字节不在 files 里）；依赖供应链另算。
- 私钥泄露 = 攻击者可签任意包；撤销只能靠部署新配置，不是即时。
- 时间戳只做记录，不做可信时间（没有 TSA）。

**2026-11-23 落地（已接线）**：`agent/extensions/plugin_signature.py` 实现规范化 JSON、文件树枚举、
受信表校验、状态/过期/撤销判定、Ed25519 验签、`package_sha256` 与文件树比对；
`plugins_installer.py` 加暂存目录 + 安装/加载双验签；`scripts/sign_plugin_package.py` 提供离线
`sign/verify`；`config/plugin_trusted_keys.json` 为空表（fail-closed）。测试：签名 **18**、接线 **8**、
CLI **2**；插件相关套件 **102 passed**。CI：`f981f6537` 的 ci.yml（run 45158672288）全绿。

## 4. 方案②：OS 级文件/网络沙箱可行性

### 4.1 两条路线

**路线 A：Windows AppContainer（推荐主路线）**

- API 链：`CreateAppContainerProfile` / `DeriveAppContainerSidFromAppContainerName`（userenv.dll）
  → `InitializeProcThreadAttributeList` + `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`
  → `CreateProcessW`（`EXTENDED_STARTUPINFO_PRESENT`）。
- 能力：默认无网络；要联网加 `internetClient` capability SID。文件访问默认拒绝，
  需对插件目录（只读）、每插件数据目录（读写）、临时目录显式 ACL 授权。
- 资源：Job Object 限内存/CPU/进程数；超时仍用现有 `PluginProcess._kill`。
- **关键工程量**：`subprocess.Popen` 不能设置 attribute list，需要一个小启动器
  （C 或 ctypes 调 `CreateProcessW` + 手工管道）。这是 3–5 天的主要成本，也是唯一能在
  「不引入 Docker」前提下做到文件/网络隔离的路线。
- 兼容性风险：Python 从用户目录加载 DLL/stdlib 会失败（需 ACL 放行安装目录）；
  插件写 `%TEMP%`、读 `USERPROFILE`、开子进程都会被拒——必须在文档里声明「沙箱插件只能用
  自己的数据目录，默认无网」。

**路线 B：Docker/WSL（可选后端，不作主路线）**

- 本机 Docker CLI 在但 **daemon 未运行**；生产是否装 Docker 未知，不能作为默认依赖。
- 可行形态：每插件一个长期容器，`docker run -i --rm --network none --read-only
  --tmpfs /tmp --memory 256m --cpus 1 --pids-limit 64 -u 65534 -v <plugin>:/plugin:ro`，
  stdin/stdout 跑现有 JSON-RPC。启动慢（秒级）、镜像供应链、Windows 路径挂载都要另算。
- 若企业部署已有 Docker，可作为「强隔离后端」二选一；不能假装 Windows 本机默认可用。

**路线 C：独立低权限用户 + ACL（不推荐单独用）**：仍是同一内核、同一用户目录可被 ACL 误配；
只能当纵深防御的一层，不能叫沙箱。

### 4.2 PoC 结果（2026-11-23 18:1x，已跑通）

环境：Windows 10 19045 / Python 3.12 / 管理员；ctypes 直调 userenv + CreateProcessW。
PoC 代码在 `.local/_poc_appcontainer.py`（**不提交**）。

| 验证项 | 结果 |
|---|---|
| 创建 AppContainer profile + SID | 成功（`S-1-15-2-...`） |
| 在容器内起进程 | 成功；`CreateProcess` 约 **1.7 ms** |
| 读仓库 `.env` | **拒绝访问**（`type` 退出码 1） |
| 访问外网 | `curl https://example.com` 超时（rc=28），默认无网络 |
| 写自己的数据目录 | 成功（ACL 授予 SID 后写入 `ac_write.txt`） |
| 起 Python | 默认失败（rc=5，拒绝访问）；对解释器目录授予 SID `(OI)(CI)(RX)` 后成功 |
| 环境变量 | 显式最小 env 块下，父进程注入的假 `FLASK_API_TOKEN` / `YUNSHU_POC_SECRET` 都**不可见** |
| `cmd.exe` / System32 | 默认可读可执行（ALL APPLICATION PACKAGES 已有权限） |

**关键工程结论（生产接线前必须处理）**：

1. **env 块不是可选的**：`CreateProcessW` 传最小 env 时要包含 `USERPROFILE` / `APPDATA` /
   `LOCALAPPDATA` / `HOMEDRIVE` / `HOMEPATH` / `USERNAME` / `PATHEXT` / `SYSTEMDRIVE` 等，
   否则返回 **error 203（ERROR_ENVVAR_NOT_FOUND）**；现有 `plugin_process._worker_env` 需补这几项。
2. **Python 解释器要显式 ACL**：每个插件 AppContainer SID 对 Python 安装目录 `(OI)(CI)(RX)`；
   卸载时删 ACE。更干净的做法是复制一份只读运行时到受控目录，避免给系统 Python 加 ACE。
3. **不要把仓库根授予容器**：仓库根有 `.env`；现有 `python -m agent.extensions.plugin_worker`
   需要仓库在 `PYTHONPATH` ⇒ 生产要把一个最小 runner 复制到插件数据目录，或单独运行时目录。
4. 资源限制仍用 Job Object（内存/CPU/进程数）；超时仍用现有 `PluginProcess._kill` 的硬杀。
5. 网络默认关闭；若某插件确需联网，再单独加 `internetClient` capability，不做全局放行。

**结论**：AppContainer 在本机可行，文件/网络/环境变量三层都能隔离；剩余是启动器工程化
（约 250–350 行 ctypes + ACL 生命周期 + runner 打包），估 3–5 天，建议下一步单独一批做，
开关默认关（`CP_PLUGIN_SANDBOX=off|appcontainer`）。

## 5. 方案③：HTTP 插件路由硬超时（为什么不能顺手做）

- 现状：14 个内置插件 / 226 条路由都在宿主进程；waitress 16 线程；bulkhead 只限每插件并发 4，
  满即 429——**限并发不是硬超时**。
- Python 线程**杀不掉**：`threading` 没有安全终止；`PyThreadState_SetAsyncExc` 只能注入异常，
  遇到 C 扩展/阻塞 IO 无效。所以「在宿主里加个 timer 然后返回 504」是**软超时**，
  线程还占着，不能算硬超时。
**2026-11-23 实测（`/metrics` 直方图，当前进程启动至今）**：指标 endpoint 标签形如
`blueprint.view`，可按前缀聚合到插件；下表是各插件全部路由的聚合分位（桶 5ms–10s）：

| 插件 | 请求数 | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| system_tools | 1369 | 0.010s | 0.025s | 0.500s |
| memory | 201 | 0.025s | 0.050s | 0.075s |
| skills | 25 | 0.050s | 0.250s | 0.500s |
| status | 11 | 0.005s | 0.075s | 0.075s |
| chat | 10 | 0.005s | 0.025s | 0.025s |
| admin_api | 8 | 0.005s | 0.005s | 0.005s |
| mcp_scheduler | 7 | 0.005s | 0.050s | 0.050s |
| admin | 5 | 0.005s | 0.010s | 0.010s |
| version_info | 4 | 0.010s | 2.500s | 2.500s |
| ingestion | 2 | 0.100s | 0.250s | 0.250s |
| safety | 2 | 0.005s | 0.005s | 0.005s |

单端点看：`system_tools.api_terminal_activity` 1368 次、p50 10ms / p99 75ms；
`version_info.api_version` 4 次里有一次冷启动落在 2.5s 桶。**结论**：当前没有持续慢路由，
bulkhead 足够；`version_info` 的冷启动值得单独看，但不构成「必须马上做硬超时」的证据。
下一步若要继续，先给「每 blueprint 的 p99/超时次数」加一条结构化指标，而不是直接改 226 条路由。

- 可选路线与代价：

| 路线 | 做法 | 代价 | 是否硬超时 |
|---|---|---|---|
| 网关超时 | 反代/客户端超时 | 只释放客户端，后端线程仍跑 | 否 |
| 每插件独立服务 | 插件路由跑独立 waitress/gunicorn，宿主反代 | Windows 无 gunicorn；14 个进程；226 条路由迁移 | 是（杀 worker） |
| 路由出进程 | 宿主把请求序列化给插件进程，回 JSON | 改 226 条路由的 request/response 形态 | 是 |
| 协作式 deadline | handler 自查 deadline，超时返回 504 | 插件不配合就无效 | 否 |

- 建议：**先量**每个 blueprint 的 p50/p99/超时次数（现有 trace/审计已够），再决定是否值得为
  某个插件单独出进程；不要为了「有超时」加一个假硬超时。

## 6. 方案④：result_schema L2 扩面方法

- 缺口：medium 24 个（另有 high 11、critical 3 也缺；本轮只谈 L2）。
- 方法（逐工具，禁止批量猜）：
  1. 在**安全环境**用代表性参数真调一次（读类工具用只读参数；写类工具用临时目录/假对象）；
  2. 记录真实返回 JSON（成功/失败各一份），按实际字段写 `result_schema`；
  3. 契约测试：成功样本 + 失败样本 + `require ok`（如果有）；
  4. 若工具有副作用，先写 fixture/假后端，不拿生产数据试。
- 24 个 medium 清单（本轮实测）：`api_multi_search_engine`、`browser_navigate`、`browser_screenshot`、
  `byted_seedance_video_generate`、`cancel_scheduled_task`、`compress`、`delegate`、
  `distill_process_from_knowledge`、`ext_discover`、`kb_discuss`、`kb_distill`、`look_at_screen`、
  `process_distill_run`、`run_tests`、`set_clipboard`、`software_install`、`stop_process`、
  `submit_task`、`web_batch`、`web_download`、`web_extract`、`web_get`、`web_post`、`workspace_write`。
- 建议先做只读、无副作用的 6 个：`web_get / web_extract / web_batch / ext_discover / kb_discuss / look_at_screen`；
  写类工具（`workspace_write / software_install / run_tests / set_clipboard`）放到后面，逐个人工确认。
- 验收：每个工具一条成功样本 + 一条失败样本；`test_l3_result_schema` 的同类测试扩到 L2。

## 7. 建议顺序与 Owner 决策

**Owner 2026-11-23 13:16 已确认**：D1 = Owner 离线私钥；D2 = 接受 AppContainer 默认无网/无用户目录；
D3 = Docker 仅可选后端；D4 = 方案 + 校验器（不接线）。本轮按此执行，未动安装器。

**后续建议**：②做 1 天 PoC，③④⑤保持 open；若 Owner 更关心「即使被签名也要限制文件/网络」：先做②的 PoC。

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| D1 | 签名主体 | Owner 离线私钥 / 发布经理二级私钥 / 暂不签 | 决定 §3.2 与发布流程 |
| D2 | 沙箱默认语义 | AppContainer（默认无网、无用户目录）/ 只做进程边界 | 决定②是否值得 3–5 天 |
| D3 | Docker | 不允许 / 可选后端 | 决定是否写第二条适配 |
| D4 | 本轮范围 | 只出方案 / 方案+校验器 / 直接接线 | 决定是否动安装器 |

## 8. 本轮边界

**已做**：签名校验器 + 离线签名工具 + 安装暂存/加载复验 + `CP_PLUGIN_SIGNATURE_MODE` 登记；
AppContainer 可行性 PoC；插件路由 p99 实测。

**仍不做**：

- 不把 AppContainer 接进生产 `plugin_process`（PoC 只在 `.local/`，开关未加）；
- 不把 Job Object 叫沙箱；不改 226 条内置路由，不加假硬超时；
- 不批量补 result_schema，不猜字段；
- 不打开插件包默认开关，不改白名单；
- 不接插件 UI iframe。
