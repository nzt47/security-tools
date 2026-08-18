/**
 * CodeEditorPanel —— 代码编辑器面板（轻量）
 * ------------------------------------------------
 * 实现：textarea 编辑 + highlight.js 实时高亮预览（防抖 400ms）。
 * 状态：内容与语言持久化到 localStorage（yunshu:editor:code:v1），
 *       Electron 多窗口共享同一 session，独立窗口可读到同一内容。
 *
 * 设计取舍【简易】：
 *  - 不引入 CodeMirror/Monaco（体积大），textarea + 高亮预览已覆盖
 *    "对话中提取代码 → 落盘编辑" 的核心诉求；
 *  - 编辑与预览上下分栏，高亮只读，防止编辑与渲染互相干扰。
 */
import { useEffect, useRef, useState } from 'react';
import hljs from 'highlight.js/lib/common';
import { Eraser, FileCode2 } from 'lucide-react';

const STORAGE_KEY = 'yunshu:editor:code:v1';

const LANGUAGES: { id: string; label: string }[] = [
  { id: 'typescript', label: 'TypeScript' },
  { id: 'javascript', label: 'JavaScript' },
  { id: 'python', label: 'Python' },
  { id: 'json', label: 'JSON' },
  { id: 'bash', label: 'Bash' },
  { id: 'plaintext', label: '纯文本' },
];

const SAMPLE: Record<string, string> = {
  typescript: `// 云枢 · 示例 TypeScript
interface AgentTask {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'done';
}

const tasks: AgentTask[] = [];

function runAgent(task: AgentTask): Promise<void> {
  return new Promise((resolve) => {
    task.status = 'running';
    setTimeout(() => {
      task.status = 'done';
      resolve();
    }, 1000);
  });
}
`,
  javascript: `// 云枢 · 示例 JavaScript
const socket = new WebSocket('wss://hub.example.com');

socket.onmessage = (ev) => {
  const { type, payload } = JSON.parse(ev.data);
  console.log(\`[hub] \${type}\`, payload);
};
`,
  python: `# 云枢 · 示例 Python
import asyncio

async def agent_loop(query: str):
    print(f"收到问题: {query}")
    for step in range(3):
        await asyncio.sleep(0.5)
        print(f"步骤 {step + 1}: 执行中...")
    return "完成"

if __name__ == "__main__":
    asyncio.run(agent_loop("你好"))
`,
  json: `{
  "agent": "云枢",
  "version": "0.1.0",
  "panels": ["nav", "chat", "think", "code"],
  "features": {
    "stream": "sse",
    "multiWindow": true
  }
}
`,
  bash: `#!/usr/bin/env bash
# 云枢 · 示例 Shell
echo "启动本地后端..."
python app_server.py &
wait $!
`,
  plaintext: '云枢 · 纯文本示例\n\n在这里粘贴任意代码片段。',
};

interface PersistedState {
  lang: string;
  content: string;
}

function loadState(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as PersistedState;
      if (typeof parsed?.content === 'string' && typeof parsed?.lang === 'string') {
        return parsed;
      }
    }
  } catch {
    // 损坏数据忽略，回退默认
  }
  return { lang: 'typescript', content: SAMPLE['typescript'] ?? '' };
}

export function CodeEditorPanel() {
  const [state, setState] = useState<PersistedState>(loadState);
  const [html, setHtml] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 防抖高亮渲染（编辑时每次输入重算）
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const lang = LANGUAGES.some((l) => l.id === state.lang) ? state.lang : 'plaintext';
      const highlighted =
        lang === 'plaintext'
          ? state.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          : hljs.highlight(state.content, { language: lang }).value;
      setHtml(`<code class="hljs">${highlighted}</code>`);
    }, 400);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [state.content, state.lang]);

  // 持久化（写 localStorage，跨窗口共享）
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // 存储不可用时静默（只读模式）
    }
  }, [state]);

  const updateContent = (content: string) => setState((s) => ({ ...s, content }));
  const changeLang = (lang: string) => setState((s) => ({ ...s, lang }));
  const clearAll = () => setState((s) => ({ ...s, content: '' }));
  const loadSample = () => {
    const sample = SAMPLE[state.lang] ?? SAMPLE['typescript'];
    setState((s) => ({ ...s, content: sample ?? '' }));
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 工具条：语言选择 + 示例 / 清空 */}
      <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 py-2">
        <FileCode2 size={13} className="text-cyan-400/80" />
        <select
          className="wb-editor-lang"
          value={state.lang}
          onChange={(e) => changeLang(e.target.value)}
          aria-label="代码语言"
        >
          {LANGUAGES.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className="wb-chip !py-1" onClick={loadSample}>
            示例代码
          </button>
          <button
            type="button"
            className="wb-chip !py-1"
            onClick={clearAll}
            title="清空内容"
          >
            <Eraser size={11} />
            清空
          </button>
        </div>
      </div>

      {/* 编辑 / 预览 上下分栏 */}
      <div className="flex min-h-0 flex-1 flex-col">
        <textarea
          ref={textareaRef}
          className="wb-editor-input"
          value={state.content}
          onChange={(e) => updateContent(e.target.value)}
          spellCheck={false}
          placeholder="在这里编写或粘贴代码…（自动高亮预览）"
        />
        <div className="wb-editor-preview">
          <div className="wb-editor-preview-title">高亮预览</div>
          <pre className="!m-0 wb-editor-pre">
            <span dangerouslySetInnerHTML={{ __html: html }} />
          </pre>
        </div>
      </div>
    </div>
  );
}
