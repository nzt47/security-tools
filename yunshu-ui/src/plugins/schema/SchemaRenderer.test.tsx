/**
 * SchemaRenderer 单元测试（任务 T3.2）
 *
 * 覆盖验收点：每种类型字段渲染、default 填充、required 星号、嵌套 object 折叠、
 * 未知类型 / 缺失 properties 整体降级、onChange 回调正确性、提交层 min/max 与必填校验，
 * 以及 PLAN-3 §2「性格微调」schema 端到端渲染。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { SchemaRenderer } from './SchemaRenderer';

/** PLAN-3 §2 的「性格微调」schema（验收样例） */
const PERSONALITY_SCHEMA = {
  type: 'object',
  title: '性格微调',
  description: '调整云枢的拟人化性格参数',
  properties: {
    mood: {
      type: 'string',
      title: '性格基调',
      enum: ['calm', 'playful', 'serious'],
      default: 'calm',
    },
    verbosity: {
      type: 'integer',
      title: '啰嗦程度',
      minimum: 1,
      maximum: 10,
      default: 5,
    },
    topics: {
      type: 'array',
      title: '感兴趣的话题',
      items: { type: 'string' },
    },
  },
  required: ['mood'],
};

/** 受控测试壳：真实管理 value 状态，模拟调用方（纯受控、不发请求） */
function Harness({
  schema,
  initial = {},
  onSubmit,
}: {
  schema: Record<string, any>;
  initial?: Record<string, any>;
  onSubmit?: (v: Record<string, any>) => void;
}) {
  const [values, setValues] = useState<Record<string, any>>(initial);
  return <SchemaRenderer schema={schema} value={values} onChange={setValues} onSubmit={onSubmit} />;
}

describe('SchemaRenderer：各类型字段渲染', () => {
  afterEach(cleanup);

  const ALL_TYPES_SCHEMA = {
    type: 'object',
    properties: {
      mood: { type: 'string', title: '基调', enum: ['a', 'b'], default: 'a' },
      name: { type: 'string', title: '名称' },
      notes: { type: 'string', title: '备注', format: 'textarea' },
      count: { type: 'integer', title: '次数', minimum: 0, maximum: 100 },
      ratio: { type: 'number', title: '比例' },
      enabled: { type: 'boolean', title: '启用' },
      tags: { type: 'array', title: '标签', items: { type: 'string' } },
    },
  };

  it('select / input / textarea / number / switch / tags 各控件均渲染', () => {
    render(<Harness schema={ALL_TYPES_SCHEMA} />);
    // select（string+enum）
    expect(screen.getByLabelText('基调').tagName).toBe('SELECT');
    // input（string）
    expect(screen.getByLabelText('名称').tagName).toBe('INPUT');
    // textarea（string+format:textarea）
    expect(screen.getByLabelText('备注').tagName).toBe('TEXTAREA');
    // number（integer / number）
    expect(screen.getByLabelText('次数')).toHaveAttribute('type', 'number');
    expect(screen.getByLabelText('比例')).toHaveAttribute('type', 'number');
    // switch（boolean）
    expect(screen.getByRole('switch')).toBeTruthy();
    // tags（array of string）
    expect(screen.getByPlaceholderText('输入后回车添加')).toBeTruthy();
  });

  it('number 字段透传 min/max 属性（不阻断输入）', () => {
    render(<Harness schema={ALL_TYPES_SCHEMA} />);
    expect(screen.getByLabelText('次数')).toHaveAttribute('min', '0');
    expect(screen.getByLabelText('次数')).toHaveAttribute('max', '100');
  });

  it('数组元素带 enum 时 TagsField 渲染可选值下拉', () => {
    render(
      <Harness
        schema={{
          type: 'object',
          properties: {
            cats: {
              type: 'array',
              title: '分类',
              items: { type: 'string', enum: ['硬件感知', '网络感知'] },
            },
          },
        }}
      />,
    );
    expect(screen.getByLabelText('分类（从可选值添加）')).toBeTruthy();
  });
});

describe('SchemaRenderer：default 填充与 required 星号', () => {
  afterEach(cleanup);

  it('缺失字段用 schema.default 填充（select 选中默认值、number 显示默认值）', () => {
    render(<Harness schema={PERSONALITY_SCHEMA} />);
    expect((screen.getByLabelText('性格基调') as HTMLSelectElement).value).toBe('calm');
    expect((screen.getByLabelText('啰嗦程度') as HTMLInputElement).value).toBe('5');
  });

  it('已有值不被 default 覆盖（调用方值优先）', () => {
    render(<Harness schema={PERSONALITY_SCHEMA} initial={{ mood: 'serious' }} />);
    expect((screen.getByLabelText('性格基调') as HTMLSelectElement).value).toBe('serious');
  });

  it('required 字段标红色星号，非必填不标', () => {
    render(<Harness schema={PERSONALITY_SCHEMA} />);
    const stars = screen.getAllByText('*');
    expect(stars).toHaveLength(1); // 仅 mood 必填
    // 星号是 label 的兄弟元素（label 文本保持精确可匹配）
    const label = screen.getByText('性格基调');
    expect(label.nextElementSibling?.textContent).toBe('*');
    expect(screen.getByText('啰嗦程度').nextElementSibling).toBeNull();
  });

  it('提交按钮存在时，onSubmit 收到含 default 的完整值', () => {
    const onSubmit = vi.fn();
    render(<Harness schema={PERSONALITY_SCHEMA} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).toHaveBeenCalledWith({ mood: 'calm', verbosity: 5 });
  });
});

describe('SchemaRenderer：嵌套 object 折叠分组', () => {
  afterEach(cleanup);

  const NESTED_SCHEMA = {
    type: 'object',
    title: '高级配置',
    properties: {
      nickname: { type: 'string', title: '昵称' },
      personality: {
        type: 'object',
        title: '人格参数',
        description: '拟人化参数',
        properties: {
          tone: { type: 'string', title: '语气', default: '温柔' },
          enabled: { type: 'boolean', title: '启用', default: true },
          extra: {
            type: 'object',
            title: '深层分组',
            properties: {
              deep: { type: 'string', title: '深层字段' },
            },
          },
        },
        required: ['tone'],
      },
    },
  };

  it('第一层嵌套默认展开，点击标题可折叠/展开', () => {
    render(<Harness schema={NESTED_SCHEMA} />);
    // 第一层「人格参数」默认展开：内部字段可见
    expect((screen.getByLabelText('语气') as HTMLInputElement).value).toBe('温柔');
    expect(screen.getByRole('switch')).toBeTruthy();
    // 点击标题折叠 → 内部字段隐藏
    fireEvent.click(screen.getByText('人格参数'));
    expect(screen.queryByLabelText('语气')).toBeNull();
    // 再点展开
    fireEvent.click(screen.getByText('人格参数'));
    expect(screen.getByLabelText('语气')).toBeTruthy();
  });

  it('嵌套 object 的 required 同样标星号', () => {
    render(<Harness schema={NESTED_SCHEMA} />);
    // 顶层 nickname 非必填；嵌套 tone 必填 → 恰好一个星号
    expect(screen.getAllByText('*')).toHaveLength(1);
    expect(screen.getByText('语气').nextElementSibling?.textContent).toBe('*');
  });

  it('嵌套字段编辑通过 onChange 合并回父级', () => {
    const onChange = vi.fn();
    render(<SchemaRenderer schema={NESTED_SCHEMA} value={{}} onChange={onChange} />);
    const tone = screen.getByLabelText('语气');
    fireEvent.change(tone, { target: { value: '幽默' } });
    expect(onChange).toHaveBeenCalledWith({
      personality: { tone: '幽默', enabled: true, extra: {} },
    });
  });

  it('深层嵌套分组默认收起（仅第一层展开）', () => {
    render(<Harness schema={NESTED_SCHEMA} />);
    // 第一层「人格参数」默认展开，但其内部第二层「深层分组」默认收起
    expect(screen.getByLabelText('语气')).toBeTruthy();
    expect(screen.queryByLabelText('深层字段')).toBeNull();
    fireEvent.click(screen.getByText('深层分组'));
    expect(screen.getByLabelText('深层字段')).toBeTruthy();
  });
});

describe('SchemaRenderer：未知类型 / 缺失 properties 降级', () => {
  afterEach(cleanup);

  it('整个 schema 非 object（无 properties）→ JsonFallbackField 降级，可编辑', () => {
    const onChange = vi.fn();
    const { container } = render(
      <SchemaRenderer schema={{ type: 'string', title: '裸值' }} value={{}} onChange={onChange} />,
    );
    expect(container.querySelector('[data-testid="schema-renderer-degraded"]')).toBeTruthy();
    expect(screen.getByText(/已降级为 JSON 编辑/)).toBeTruthy();
    const ta = screen.getByLabelText('裸值') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: '{"k": 1}' } });
    expect(onChange).toHaveBeenCalledWith({ k: 1 });
  });

  it('空 schema {} 同样降级且不崩溃', () => {
    const onChange = vi.fn();
    const { container } = render(<SchemaRenderer schema={{}} value={{}} onChange={onChange} />);
    expect(container.querySelector('[data-testid="schema-renderer-degraded"]')).toBeTruthy();
    const ta = screen.getByLabelText('配置') as HTMLTextAreaElement;
    expect(ta.value).toBe('{}'); // 初始草稿即空对象
    fireEvent.change(ta, { target: { value: '{"k": 1}' } });
    expect(onChange).toHaveBeenCalledWith({ k: 1 });
  });

  it('降级 textarea 输入非法 JSON → 红字报错、不回调', () => {
    const onChange = vi.fn();
    render(<SchemaRenderer schema={{ type: 'array' }} value={{}} onChange={onChange} />);
    const ta = screen.getByLabelText('配置') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: '{oops' } });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('JSON 解析失败');
  });

  it('object schema 内未知类型字段 → 该字段降级为 JSON 编辑（其余字段正常）', () => {
    const schema = {
      type: 'object',
      properties: {
        normal: { type: 'string', title: '正常字段' },
        weird: { type: 'blob', title: '未知字段' },
      },
    };
    const onChange = vi.fn();
    render(<SchemaRenderer schema={schema} value={{ weird: { x: 1 } }} onChange={onChange} />);
    // 正常字段仍渲染
    expect(screen.getByLabelText('正常字段')).toBeTruthy();
    // 未知字段降级为 JSON textarea
    const ta = screen.getByLabelText('未知字段') as HTMLTextAreaElement;
    expect(ta.value).toContain('"x"');
    fireEvent.change(ta, { target: { value: '{"x": 2}' } });
    expect(onChange).toHaveBeenCalledWith({ weird: { x: 2 } });
  });

  it('object schema 内非 string 元素数组 → 该字段降级为 JSON 编辑', () => {
    const schema = {
      type: 'object',
      properties: {
        matrix: { type: 'array', title: '矩阵', items: { type: 'number' } },
      },
    };
    const { container } = render(<SchemaRenderer schema={schema} value={{}} onChange={() => {}} />);
    expect(container.querySelector('[data-testid="schema-renderer-degraded"]')).toBeNull();
    expect(screen.getByLabelText('矩阵').tagName).toBe('TEXTAREA');
  });
});

describe('SchemaRenderer：onChange 回调正确性', () => {
  afterEach(cleanup);

  it('编辑任意字段 → onChange 收到「其余字段 + 新值」的完整对象', () => {
    const onChange = vi.fn();
    render(<SchemaRenderer schema={PERSONALITY_SCHEMA} value={{}} onChange={onChange} />);

    // select
    fireEvent.change(screen.getByLabelText('性格基调'), { target: { value: 'playful' } });
    expect(onChange).toHaveBeenLastCalledWith({ mood: 'playful', verbosity: 5 });

    // number
    fireEvent.change(screen.getByLabelText('啰嗦程度'), { target: { value: '8' } });
    expect(onChange).toHaveBeenLastCalledWith({ mood: 'calm', verbosity: 8 });

    // tags：回车添加
    const tagInput = screen.getByPlaceholderText('输入后回车添加') as HTMLInputElement;
    fireEvent.change(tagInput, { target: { value: 'AI' } });
    fireEvent.keyDown(tagInput, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith({ mood: 'calm', verbosity: 5, topics: ['AI'] });
  });

  it('switch 切换 → onChange 收到布尔值', () => {
    const schema = {
      type: 'object',
      properties: { enabled: { type: 'boolean', title: '启用', default: false } },
    };
    const onChange = vi.fn();
    render(<SchemaRenderer schema={schema} value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith({ enabled: true });
  });
});

describe('SchemaRenderer：提交层校验', () => {
  afterEach(cleanup);

  it('数值超出 min/max → 提交被拦截并红字提示，onSubmit 不触发', () => {
    const onSubmit = vi.fn();
    render(<Harness schema={PERSONALITY_SCHEMA} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText('啰嗦程度'), { target: { value: '11' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('不能大于 10');

    // 修正后提交成功
    fireEvent.change(screen.getByLabelText('啰嗦程度'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).toHaveBeenCalledWith({ mood: 'calm', verbosity: 3 });
  });

  it('必填字段缺失（无默认值且为空）→ 提交被拦截', () => {
    const schema = {
      type: 'object',
      properties: { token: { type: 'string', title: '令牌' } },
      required: ['token'],
    };
    const onSubmit = vi.fn();
    render(<Harness schema={schema} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('「令牌」为必填项');
  });

  it('嵌套 object 的 min/max 递归校验', () => {
    const schema = {
      type: 'object',
      properties: {
        nested: {
          type: 'object',
          title: '内层',
          properties: { level: { type: 'number', title: '等级', minimum: 1, maximum: 3, default: 2 } },
        },
      },
    };
    const onSubmit = vi.fn();
    render(<Harness schema={schema} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText('等级'), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('不能大于 3');
  });

  it('无 onSubmit 时不渲染提交按钮', () => {
    render(<Harness schema={PERSONALITY_SCHEMA} />);
    expect(screen.queryByRole('button', { name: '提交' })).toBeNull();
  });
});

describe('SchemaRenderer：PLAN-3 §2 性格微调 schema 端到端', () => {
  afterEach(cleanup);

  it('渲染完整表单并可提交收集值', () => {
    const onSubmit = vi.fn();
    render(<Harness schema={PERSONALITY_SCHEMA} onSubmit={onSubmit} />);

    // 标题与说明
    expect(screen.getByText('性格微调')).toBeTruthy();
    expect(screen.getByText('调整云枢的拟人化性格参数')).toBeTruthy();

    // select 三选项 + default
    const select = screen.getByLabelText('性格基调') as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(options).toEqual(['calm', 'playful', 'serious']);
    expect(select.value).toBe('calm');

    // number 默认 5
    expect((screen.getByLabelText('啰嗦程度') as HTMLInputElement).value).toBe('5');

    // tags 输入
    const tagInput = screen.getByPlaceholderText('输入后回车添加') as HTMLInputElement;
    fireEvent.change(tagInput, { target: { value: '哲学' } });
    fireEvent.keyDown(tagInput, { key: 'Enter' });

    // 提交收集完整值（default 已填充 + 新标签）
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(onSubmit).toHaveBeenCalledWith({ mood: 'calm', verbosity: 5, topics: ['哲学'] });
  });
});
