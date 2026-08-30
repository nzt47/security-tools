/**
 * 字段组件单元测试（任务 T3.2）
 *
 * 覆盖：每个受控字段组件的渲染与 onChange 正确性（Select / Input / Textarea / Number /
 * Switch / Tags / ObjectGroup / JsonFallbackField）。
 * 使用 fireEvent（项目未引入 @testing-library/user-event）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { SelectField } from './fields/SelectField';
import { InputField } from './fields/InputField';
import { TextareaField } from './fields/TextareaField';
import { NumberField } from './fields/NumberField';
import { SwitchField } from './fields/SwitchField';
import { TagsField } from './fields/TagsField';
import { ObjectGroup } from './fields/ObjectGroup';
import { JsonFallbackField } from './fields/JsonFallbackField';

describe('SelectField（string + enum）', () => {
  afterEach(cleanup);

  it('渲染全部选项并显示当前值', () => {
    render(
      <SelectField
        label="性格基调"
        value="calm"
        options={['calm', 'playful', 'serious']}
        onChange={() => {}}
      />,
    );
    const select = screen.getByLabelText('性格基调') as HTMLSelectElement;
    expect(select.value).toBe('calm');
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(options).toEqual(['calm', 'playful', 'serious']);
  });

  it('onChange 携带选中的值（受控）', () => {
    const onChange = vi.fn();
    render(<SelectField label="mood" value="calm" options={['calm', 'playful']} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('mood'), { target: { value: 'playful' } });
    expect(onChange).toHaveBeenCalledWith('playful');
  });
});

describe('InputField（string）', () => {
  afterEach(cleanup);

  it('渲染当前值并回调 onChange', () => {
    const onChange = vi.fn();
    render(<InputField label="名称" value="云枢" onChange={onChange} />);
    const input = screen.getByLabelText('名称') as HTMLInputElement;
    expect(input.value).toBe('云枢');
    fireEvent.change(input, { target: { value: '云枢2' } });
    expect(onChange).toHaveBeenCalledWith('云枢2');
  });

  it('必填字段带 aria-required', () => {
    render(<InputField label="name" value="" onChange={() => {}} required />);
    expect(screen.getByLabelText('name')).toHaveAttribute('aria-required', 'true');
  });
});

describe('TextareaField（string + format:textarea）', () => {
  afterEach(cleanup);

  it('渲染多行值并回调 onChange', () => {
    const onChange = vi.fn();
    render(<TextareaField label="说明" value="你好" onChange={onChange} rows={4} />);
    const ta = screen.getByLabelText('说明') as HTMLTextAreaElement;
    expect(ta.value).toBe('你好');
    expect(ta.rows).toBe(4);
    fireEvent.change(ta, { target: { value: '新文本' } });
    expect(onChange).toHaveBeenCalledWith('新文本');
  });
});

describe('NumberField（integer/number）', () => {
  afterEach(cleanup);

  it('渲染数值并透传 min/max', () => {
    render(<NumberField label="啰嗦程度" value={5} min={1} max={10} onChange={() => {}} />);
    const input = screen.getByLabelText('啰嗦程度') as HTMLInputElement;
    expect(input.value).toBe('5');
    expect(input).toHaveAttribute('min', '1');
    expect(input).toHaveAttribute('max', '10');
    expect(input).toHaveAttribute('type', 'number');
  });

  it('输入合法数字 → onChange(number)；清空 → onChange(undefined)', () => {
    const onChange = vi.fn();
    render(<NumberField label="count" value={5} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('count'), { target: { value: '7' } });
    expect(onChange).toHaveBeenCalledWith(7);
    fireEvent.change(screen.getByLabelText('count'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it('空值渲染为空输入（不显示 NaN/undefined）', () => {
    render(<NumberField label="count" value={undefined} onChange={() => {}} />);
    expect((screen.getByLabelText('count') as HTMLInputElement).value).toBe('');
  });
});

describe('SwitchField（boolean）', () => {
  afterEach(cleanup);

  it('以 role=switch 渲染并切换 onChange', () => {
    const onChange = vi.fn();
    const { rerender } = render(<SwitchField label="规划引擎" value={false} onChange={onChange} />);
    const sw = screen.getByRole('switch') as HTMLInputElement;
    expect(sw.checked).toBe(false);
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
    // 受控：父级把 value 更新为 true 后，再次点击应回调 false
    rerender(<SwitchField label="规划引擎" value={true} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it('受控：value=true 时 checked', () => {
    render(<SwitchField label="enabled" value={true} onChange={() => {}} />);
    expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true);
  });
});

describe('TagsField（array of string）', () => {
  afterEach(cleanup);

  it('渲染标签芯片，回车添加并去重', () => {
    const onChange = vi.fn();
    render(<TagsField label="话题" value={['AI']} onChange={onChange} />);
    expect(screen.getByText('AI')).toBeTruthy();

    const input = screen.getByPlaceholderText('输入后回车添加') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'NLP' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith(['AI', 'NLP']);

    // 重复标签不添加
    fireEvent.change(input, { target: { value: 'AI' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).not.toHaveBeenCalledWith(['AI', 'AI']);
  });

  it('从可选值下拉添加（过滤已选）', () => {
    const onChange = vi.fn();
    render(
      <TagsField
        label="分类"
        value={['硬件感知']}
        options={['硬件感知', '网络感知', '文件感知']}
        onChange={onChange}
      />,
    );
    const select = screen.getByLabelText('分类（从可选值添加）') as HTMLSelectElement;
    const optionValues = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    // 已选的「硬件感知」不再出现在候选里
    expect(optionValues).toEqual(['', '网络感知', '文件感知']);
    fireEvent.change(select, { target: { value: '网络感知' } });
    expect(onChange).toHaveBeenCalledWith(['硬件感知', '网络感知']);
  });

  it('点击移除按钮回调 onChange（去掉该标签）', () => {
    const onChange = vi.fn();
    render(<TagsField label="tags" value={['a', 'b']} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('移除a'));
    expect(onChange).toHaveBeenCalledWith(['b']);
  });
});

describe('ObjectGroup（嵌套 object 折叠分组）', () => {
  afterEach(cleanup);

  it('defaultOpen=false 默认收起，点击标题展开，aria-expanded 同步', () => {
    render(
      <ObjectGroup title="人格">
        <div>内部字段</div>
      </ObjectGroup>,
    );
    expect(screen.queryByText('内部字段')).toBeNull();
    const header = screen.getByText('人格');
    fireEvent.click(header);
    expect(screen.getByText('内部字段')).toBeTruthy();
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(header);
    expect(screen.queryByText('内部字段')).toBeNull();
  });

  it('defaultOpen=true 默认展开', () => {
    render(
      <ObjectGroup title="组" defaultOpen>
        <div>可见</div>
      </ObjectGroup>,
    );
    expect(screen.getByText('可见')).toBeTruthy();
  });
});

describe('JsonFallbackField（未知类型降级）', () => {
  afterEach(cleanup);

  it('将对象值格式化为 JSON 文本', () => {
    render(<JsonFallbackField label="raw" value={{ a: 1 }} onChange={() => {}} />);
    const ta = screen.getByLabelText('raw') as HTMLTextAreaElement;
    expect(ta.value).toContain('"a"');
    expect(ta.value).toContain('1');
  });

  it('合法 JSON 编辑 → onChange(解析结果)；非法 → 红字提示且不回调', () => {
    const onChange = vi.fn();
    render(<JsonFallbackField label="raw" value={{ a: 1 }} onChange={onChange} />);
    const ta = screen.getByLabelText('raw') as HTMLTextAreaElement;

    fireEvent.change(ta, { target: { value: '{"a": 2}' } });
    expect(onChange).toHaveBeenCalledWith({ a: 2 });
    expect(screen.queryByRole('alert')).toBeNull();

    fireEvent.change(ta, { target: { value: '{bad json' } });
    expect(onChange).toHaveBeenCalledTimes(1); // 非法不回调
    expect(screen.getByRole('alert').textContent).toContain('JSON 解析失败');
  });

  it('外部 value 变化时同步草稿（合法输入不被格式化打断）', () => {
    const { rerender } = render(<JsonFallbackField label="raw" value={{ a: 1 }} onChange={() => {}} />);
    // 外部变为 {a: 2} → 草稿同步
    rerender(<JsonFallbackField label="raw" value={{ a: 2 }} onChange={() => {}} />);
    expect((screen.getByLabelText('raw') as HTMLTextAreaElement).value).toContain('"a": 2');
  });
});
