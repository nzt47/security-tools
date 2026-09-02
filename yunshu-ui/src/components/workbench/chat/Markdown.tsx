/**
 * Markdown 渲染器
 * - remark-gfm      ：表格 / 删除线 / 任务列表等 GFM 语法
 * - rehype-highlight：代码块语法高亮（highlight.js）
 * - 自定义 pre      ：代码块外层加"语言标签 + 复制"工具条
 */
import { Children, isValidElement, useState, type ReactElement, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Check, Copy } from 'lucide-react';

/** 从 <pre> 的子 <code> 元素上取语言名（className="language-xxx"） */
function extractLanguage(codeElement: ReactNode): string {
  if (!isValidElement<{ className?: string }>(codeElement)) return 'text';
  const match = /language-([\w+-]+)/.exec(codeElement.props.className ?? '');
  return match ? match[1] : 'text';
}

function extractCodeText(codeElement: ReactNode): string {
  if (!isValidElement<{ children?: ReactNode }>(codeElement)) return '';
  return String(codeElement.props.children ?? '');
}

function CodeBlock({
  language,
  text,
  children,
}: {
  language: string;
  text: string;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用时静默失败（如非 HTTPS 环境）
    }
  };

  return (
    <div className="wb-codeblock">
      <div className="wb-codeblock-bar">
        <span className="wb-codeblock-lang">{language}</span>
        <button className="wb-codeblock-copy" type="button" onClick={copy} title="复制代码">
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="wb-codeblock-pre">{children}</pre>
    </div>
  );
}

export function Markdown({ content }: { content: string }) {
  return (
    <div className="wb-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // 块级代码：外层 pre 替换为带工具条的容器；
          // children 是 rehype-highlight 高亮后的 code 元素，必须原样保留以保证语法高亮
          pre: ({ children }) => {
            const child = Children.only(children);
            const language = extractLanguage(child);
            const text = extractCodeText(child);
            return text ? (
              <CodeBlock language={language} text={text}>
                {children}
              </CodeBlock>
            ) : (
              <pre>{children}</pre>
            );
          },
          // 行内代码：单色点缀，由 .wb-markdown code 统一样式处理
          code: ({ className, children, ...props }) => {
            const isBlock = /language-/.test(className ?? '');
            return isBlock ? (
              <code className={className} {...props}>
                {children}
              </code>
            ) : (
              <code className="wb-inline-code" {...props}>
                {children}
              </code>
            );
          },
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer" className="wb-markdown-link">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
