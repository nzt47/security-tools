export interface ParsedQuote {
  quoted: string;
  rest: string;
}

/** 从用户消息正文里取出引用块（本仓引用格式：首行起以 '> ' 开头的一整段 + 一个空行分隔） */
export function parseLeadingQuote(content: string): ParsedQuote | null {
  const text = String(content || '');
  if (!text.startsWith('> ')) return null;
  const lines = text.split('\n');
  const quoted: string[] = [];
  let i = 0;
  for (; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.startsWith('> ')) {
      quoted.push(line.slice(2));
      continue;
    }
    if (!line.trim() && quoted.length) {
      i += 1;
      break;
    }
    if (!line.trim()) continue;
    break;
  }
  if (!quoted.length) return null;
  return { quoted: quoted.join(' ').trim(), rest: lines.slice(i).join('\n').trim() };
}

/** 在消息列表里找「被引用的那条」：内容归一化后以引用文本开头，取**最后一条**（最近一次） */
export function findQuotedMessage<T extends { id: string; content?: string }>(
  messages: T[],
  quoted: string,
): T | null {
  const norm = (s: string) => String(s || '').replace(/\s+/g, ' ').trim();
  const needle = norm(quoted).slice(0, 40);
  if (!needle) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    const body = norm(m.content || '');
    if (body.includes(needle)) return m;
  }
  return null;
}
