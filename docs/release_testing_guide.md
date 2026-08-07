# Release 自动化测试避坑指南

> 版本：v1.0.0 · 2026-08-07
> 适用范围：release 发布流程三件套（Shell 函数库 / WinForms 引导脚本 / Docker 镜像）的本地自动化验证。
> 所有坑位均来自真实实测（pip 包 15 项单测、curl_http_code 网络失败演示、WinForms GUI UIA 自动化冒烟），非纸面推测。

---

## 1. 验证闭环总览（2026-08-07 实测）

| 验证项 | 方法 | 结果 |
|---|---|---|
| pip 包 `release-shell-lib` | `python -m unittest discover -s packages/release_shell_lib/tests -v` | Ran 15 tests OK |
| `curl_http_code` 网络失败映射 | 本地 ThreadingHTTPServer + 关闭/坏 DNS/黑洞地址 | 在线 200；拒连/DNS/超时均 500 |
| WinForms GUI 按钮与日志 | UIA + 原生 Win32 混合驱动（pwsh -Sta） | 非法版本 FAIL 日志 / 合法版本确认框点「否」正确中止 |

---

## 2. WinForms GUI 自动化测试坑（UIA 驱动）

### 坑 2.1 线程模型：pwsh 默认 MTA，WinForms 必须 STA

- **现象**：GUI 进程启动即退出，主窗口找不到；GUI 脚本内 STA 检测（`ApartmentState -ne 'STA'`）直接 exit 1。
- **根因**：pwsh 7 默认以 MTA（多线程单元）运行脚本线程，WinForms 控件要求 STA。
- **解决**：启动一律加 `-Sta`：

```powershell
Start-Process pwsh -ArgumentList "-Sta", "-NoProfile", "-File", $guiPath -PassThru
```

### 坑 2.2 按钮点击：UIA InvokePattern 与 Application.DoEvents() 死锁

- **现象**：`InvokePattern.Invoke()` 抛 `HRESULT 0x80131505 Operation timed out`，GUI 卡死。
- **根因**：GUI 日志函数 `Add-Log` 内含 `[Application]::DoEvents()`；测试侧同步 Invoke 会重入消息泵导致死锁。
- **解决**：改用 Win32 `PostMessage(hwnd, BM_CLICK=0x00F5)` **异步**投递：

```csharp
[DllImport("user32.dll")]
public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
```

```powershell
[Win32Gui]::PostMessage($hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
```

### 坑 2.3 RichTextBox 定位：AutomationId 被覆盖、类名带随机后缀

- **现象**：控件设置了 `$logBox.Name = "logBox"`，但 UIA 枚举到的 AutomationId 是**窗口句柄**（如 `3869254`），ClassName 带随机后缀（`WindowsForms10.RICHEDIT50W.app.0.8138b3_r3_ad1`），`PropertyCondition` 不支持通配符，按 Name/ClassName 均定位失败。
- **解决**：用 `ControlType.Document` 定位（窗口内唯一）：

```powershell
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Document)
$log = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
```

### 坑 2.4 脚本编码：PowerShell 5.1 读无 BOM UTF-8 中文乱码

- **现象**：`powershell -File` 把无 BOM UTF-8 脚本按 ANSI 解析，中文字符串截断、语法错误。
- **解决**：测试脚本写入时强制 UTF-8 BOM：

```powershell
[IO.File]::WriteAllText($path, $content, [Text.UTF8Encoding]::new($true))
```

（pwsh 7 默认 UTF-8，无 BOM 也可，但为兼容 powershell.exe 建议统一带 BOM。）

---

## 3. MessageBox 模态框定位坑（本次复验新发现）

### 坑 3.1 UIA 枚举不到 MessageBox（#32770）

- **现象**：日志已输出 `[OK] 远端无 tag（唯一性通过）`，下一步必弹确认框，但 UIA `RootElement` 的窗口树里**始终找不到**「发布操作确认」窗口，测试误判「确认框未出现」。
- **根因**：MessageBox 由 Win32 模态对话框提供，此环境下不出现在 UIA 窗口树中（UIA 侧主窗口 IsEnabled 也不反映模态禁用状态）。
- **解决**：改用原生 Win32 `EnumWindows + GetWindowText` 按标题找对话框，`EnumChildWindows` 找子控件：

```powershell
# 顶层对话框
[DllImport("user32.dll")]
public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
# 子控件
[DllImport("user32.dll")]
public static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr lParam);
[DllImport("user32.dll")]
public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
```

### 坑 3.2 找「否」按钮必须按 ClassName="Button" 过滤

- **现象**：按文本匹配「否」会点到一个**非按钮控件**上，MessageBox 不关闭，流程永不进入中止分支。
- **根因**：MessageBox 的消息文本（如「是否继续？」）含"是/否"字样，该 Static 控件文本也匹配 `"否"`，`EnumChildWindows` 遍历会命中它。
- **解决**：先 `GetClassName` 过滤类名 `Button`，再匹配文本：

```powershell
$cls = New-Object System.Text.StringBuilder 64
[Win32Gui]::GetClassName($h, $cls, 64) | Out-Null
if ($cls.ToString() -ne "Button") { return $true }   # 跳过 Static 等非按钮
$sb = New-Object System.Text.StringBuilder 256
[Win32Gui]::GetWindowText($h, $sb, 256) | Out-Null
if ($sb.ToString() -match $textMatch) { $script:foundBtn = $h }
```

### 坑 3.3 UIA TextPattern 的换行被规范化为单个 \r

- **现象**：日志全文 `-split "`r?`n"` 拆不开，整段日志粘成一行，逐行断言失败。
- **根因**：UIA `TextPattern.DocumentRange.GetText(-1)` 返回的文本换行符被规范化为单个 `\r`。
- **解决**：split 用正则 `"\r\n|\r|\n"`（同时兼容三态）：

```powershell
$logText -split "\r\n|\r|\n" | ForEach-Object { Write-Host "  | $_" }
```

---

## 4. curl_http_code 验证坑

### 坑 4.1 响应体文件路径不一致（CWD vs 脚本目录）

- **现象**：在线请求返回 200，但 `read_resp_file` 读不到响应体（断言假失败）。
- **根因**：`curl_http_code` 默认把响应体写入 **CWD** 的 `gh_resp.json`；演示脚本从自身目录（`.sim-gh/gh_resp.json`）读取，两处路径不一致。
- **解决**：演示/测试中显式传 `resp_file=` 与读取路径保持一致：

```python
code = curl_http_code(url, timeout=5, resp_file=RESP_FILE)
body = read_resp_file(RESP_FILE)
```

### 坑 4.2 网络失败时的响应体残留

- **现象**：网络失败（拒连/DNS/超时）后 `read_resp_file` 读到**旧文件内容**而非提示语。
- **根因**：网络层失败不写响应体文件，旧 `gh_resp.json` 残留被误读。
- **解决**：断言前先删除响应体文件再验证：

```python
if os.path.exists(RESP_FILE):
    os.remove(RESP_FILE)
```

---

## 5. 推荐测试脚本骨架（GUI 冒烟）

```powershell
# 1) 启动（pwsh -Sta）
$proc = Start-Process pwsh -ArgumentList "-Sta", "-NoProfile", "-File", $gui -PassThru

# 2) 等待主窗口（UIA）
$cond = New-Object ...PropertyCondition(NameProperty, "Release 首次发布引导 (WinForms)")
# 循环轮询 RootElement.FindFirst(Children)

# 3) 输入版本号（ValuePattern.SetValue）+ 点击按钮（PostMessage BM_CLICK）

# 4) 读日志（ControlType.Document + TextPattern，split 用 "\r\n|\r|\n"）

# 5) 确认框（EnumWindows 按标题）→ 点「否」（EnumChildWindows + ClassName=="Button" 过滤 + BM_CLICK）

# 6) 收尾 Stop-Process $proc -Force
```

---

## 6. 验证结果记录（复验 2026-08-07）

- 场景 A（非法 `v1.x`）：Step1 标签变红 `[FAIL]`，日志 `[FAIL] 版本号格式错误: v1.x`。
- 场景 B（合法 `v9.9.9`）：Step1 `[OK]` → Step2 `[WARN] 工作区存在未提交改动（30 条）` + 同步 `[OK]` → Step3 tag 唯一性 `[OK]` → 确认框弹出 → 点「否」→ 日志 `[FAIL] 未创建 tag，发布中止`，**写操作零执行**。
