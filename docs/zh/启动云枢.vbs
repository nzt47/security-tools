' 云枢 · 数字生命体 - 无窗口后台启动脚本
' 双击此文件可在后台静默启动云枢 Web 服务

Dim objShell, strCmd
Set objShell = CreateObject("WScript.Shell")

' Python 绝对路径（VBS 环境可能不加载 PATH）
Const PYTHON_PATH = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"

' 切换到项目目录并启动（隐藏命令行窗口），日志重定向到文件
Dim fs : Set fs = CreateObject("Scripting.FileSystemObject")
Dim appDir : appDir = fs.GetParentFolderName(WScript.ScriptFullName)

' 先查杀占用 5678 端口的残留进程
objShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -ano ^| findstr "":5678"" ^| findstr ""LISTENING""') do taskkill /F /PID %a >nul 2>&1", 0, True

Dim logDir : logDir = appDir & "\logs"
If Not fs.FolderExists(logDir) Then fs.CreateFolder(logDir)
Dim logFile : logFile = logDir & "\server_" & Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2) & ".log"
strCmd = "cmd /c cd /d """ & appDir & """ && """ & PYTHON_PATH & """ app_server.py >> """ & logFile & """ 2>&1"
objShell.Run strCmd, 0, False

' 等待 5 秒后打开浏览器
WScript.Sleep 5000
objShell.Run "http://127.0.0.1:5678", 1, False

Set objShell = Nothing
