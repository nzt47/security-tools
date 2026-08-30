/**
 * SchemaRenderer —— 通用 Schema 驱动表单渲染器（任务 T3.2，协议见 PLAN-3 §2/§3）。
 *
 * 给定 JSON Schema 子集 + 当前值，自动渲染完整表单：
 * - 按类型分发到 SelectField / InputField / TextareaField / NumberField / SwitchField /
 *   TagsField / ObjectGroup（嵌套折叠）；
 * - 未知 type / 缺失 properties → 整个 schema 降级为 JsonFallbackField；
 * - 渲染时用 schema.default 填充缺失字段（不原地修改调用方 value）；
 * - required 字段标红色星号；
 * - 纯受控：不发任何请求；onSubmit 可选（提交时做 required / min-max 校验并提示，不阻断输入）。
 */
import React, { useMemo, useState } from 'react';
import type { SchemaField } from './types';
import { SelectField } from './fields/SelectField';
import { InputField } from './fields/InputField';
import { TextareaField } from './fields/TextareaField';
import { NumberField } from './fields/NumberField';
import { SwitchField } from './fields/SwitchField';
import { TagsField } from './fields/TagsField';
import { ObjectGroup } from './fields/ObjectGroup';
import { JsonFallbackField } from './fields/JsonFallbackField';

export interface SchemaRendererProps {
  /** JSON Schema 子集 */
  schema: Record<string, any>;
  /** 当前值（受控） */
  value: Record<string, any>;
  /** 值变化回调 */
  onChange: (next: Record<string, any>) => void;
  /** 可选：提交回调（渲染「提交」按钮；提交时校验并提示） */
  onSubmit?: (values: Record<string, any>) => void;
}

type FieldKind = 'select' | 'input' | 'textarea' | 'number' | 'switch' | 'tags' | 'object' | 'json';

/** 按字段声明解析控件类型（未知 → json 降级） */
function resolveFieldKind(field: SchemaField): FieldKind {
  if (field.type === 'object') return 'object';
  if (field.type === 'string' && Array.isArray(field.enum) && field.enum.length > 0) return 'select';
  if (field.type === 'string' && field.format === 'textarea') return 'textarea';
  if (field.type === 'string') return 'input';
  if (field.type === 'integer' || field.type === 'number') return 'number';
  if (field.type === 'boolean') return 'switch';
  if (field.type === 'array') {
    const itemsType = field.items?.type;
    if (itemsType === undefined || itemsType === 'string') return 'tags';
    return 'json'; // 非 string 元素数组 → 降级
  }
  return 'json';
}

/** 深拷贝默认值（避免渲染间共享引用被误改） */
function cloneDefault(d: unknown): unknown {
  if (Array.isArray(d)) return d.map(cloneDefault);
  if (d !== null && typeof d === 'object') {
    return Object.fromEntries(
      Object.entries(d as Record<string, unknown>).map(([k, v]) => [k, cloneDefault(v)]),
    );
  }
  return d;
}

/**
 * 用 schema.default 填充缺失字段（渲染层；不原地修改输入 value）。
 * 嵌套 object 递归填充；null/缺失的对象字段先补空对象再递归。
 */
export function fillDefaults(
  schema: SchemaField | undefined,
  value: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(value ?? {}) };
  const props = schema?.properties ?? {};
  for (const [key, field] of Object.entries(props)) {
    const has =
      Object.prototype.hasOwnProperty.call(out, key) &&
      out[key] !== undefined &&
      out[key] !== null;
    if (!has && field.default !== undefined) {
      out[key] = cloneDefault(field.default);
    }
    if (field.type === 'object') {
      const cur = out[key];
      const base =
        cur !== null && typeof cur === 'object' && !Array.isArray(cur)
          ? (cur as Record<string, unknown>)
          : {};
      out[key] = fillDefaults(field, base);
    }
  }
  return out;
}

/** 顶层 schema 是否可结构化为对象表单（type object 或未声明 + 有 properties） */
export function isObjectSchema(
  schema: unknown,
): schema is SchemaField & { properties: Record<string, SchemaField> } {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return false;
  const s = schema as SchemaField;
  if (s.type !== undefined && s.type !== 'object') return false;
  return !!s.properties && typeof s.properties === 'object' && !Array.isArray(s.properties);
}

/** 提交层校验：required 缺失 + 数值 min/max（递归嵌套 object） */
export function validateSchema(
  schema: SchemaField,
  value: Record<string, unknown>,
): string[] {
  const issues: string[] = [];
  const required = Array.isArray(schema.required) ? schema.required : [];
  const props = schema.properties ?? {};
  for (const key of required) {
    const v = value[key];
    const missing =
      v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
    if (missing) issues.push(`「${props[key]?.title ?? key}」为必填项`);
  }
  for (const [key, field] of Object.entries(props)) {
    const v = value[key];
    if (
      (field.type === 'integer' || field.type === 'number') &&
      typeof v === 'number' &&
      !Number.isNaN(v)
    ) {
      if (field.minimum !== undefined && v < field.minimum) {
        issues.push(`「${field.title ?? key}」不能小于 ${field.minimum}`);
      }
      if (field.maximum !== undefined && v > field.maximum) {
        issues.push(`「${field.title ?? key}」不能大于 ${field.maximum}`);
      }
    }
    if (field.type === 'object') {
      const nested =
        v !== null && typeof v === 'object' && !Array.isArray(v)
          ? (v as Record<string, unknown>)
          : {};
      issues.push(...validateSchema(field, nested));
    }
  }
  return issues;
}

interface SchemaFieldsProps {
  properties: Record<string, SchemaField>;
  required?: string[];
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  /** 嵌套深度（顶层为 1；第一层 object 分组默认展开） */
  depth: number;
}

/** 字段列表渲染（顶层与嵌套 object 共用；全部受控） */
function SchemaFields({ properties, required, value, onChange, depth }: SchemaFieldsProps) {
  const entries = Object.entries(properties);
  if (entries.length === 0) {
    return <p className="text-xs text-[var(--text-muted)]">（该分组暂无字段）</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      {entries.map(([key, field]) => {
        const label = field.title ?? key;
        const description = field.description;
        const isRequired = Array.isArray(required) && required.includes(key);
        const fieldValue = value[key];
        const onFieldChange = (next: unknown) => onChange({ ...value, [key]: next });
        const common = { label, description, required: isRequired };

        switch (resolveFieldKind(field)) {
          case 'select':
            return (
              <SelectField
                key={key}
                {...common}
                value={fieldValue == null ? '' : String(fieldValue)}
                options={(Array.isArray(field.enum) ? field.enum : []).map(String)}
                onChange={(v) => onFieldChange(v)}
              />
            );
          case 'textarea':
            return (
              <TextareaField
                key={key}
                {...common}
                value={fieldValue == null ? '' : String(fieldValue)}
                onChange={(v) => onFieldChange(v)}
              />
            );
          case 'input':
            return (
              <InputField
                key={key}
                {...common}
                value={fieldValue == null ? '' : String(fieldValue)}
                onChange={(v) => onFieldChange(v)}
              />
            );
          case 'number':
            return (
              <NumberField
                key={key}
                {...common}
                value={typeof fieldValue === 'number' && !Number.isNaN(fieldValue) ? fieldValue : undefined}
                min={field.minimum}
                max={field.maximum}
                onChange={(v) => onFieldChange(v)}
              />
            );
          case 'switch':
            return (
              <SwitchField key={key} {...common} value={!!fieldValue} onChange={(v) => onFieldChange(v)} />
            );
          case 'tags':
            return (
              <TagsField
                key={key}
                {...common}
                value={Array.isArray(fieldValue) ? fieldValue.filter((x): x is string => typeof x === 'string') : []}
                options={field.items?.enum ? (field.items.enum as unknown[]).map(String) : undefined}
                onChange={(v) => onFieldChange(v)}
              />
            );
          case 'object':
            return (
              <ObjectGroup key={key} title={label} description={description} defaultOpen={depth === 1}>
                <SchemaFields
                  properties={field.properties ?? {}}
                  required={field.required}
                  value={
                    fieldValue !== null && typeof fieldValue === 'object' && !Array.isArray(fieldValue)
                      ? (fieldValue as Record<string, unknown>)
                      : {}
                  }
                  onChange={(next) => onFieldChange(next)}
                  depth={depth + 1}
                />
              </ObjectGroup>
            );
          default:
            return (
              <JsonFallbackField key={key} {...common} value={fieldValue} onChange={(v) => onFieldChange(v)} />
            );
        }
      })}
    </div>
  );
}

export function SchemaRenderer({ schema, value, onChange, onSubmit }: SchemaRendererProps) {
  const [submitError, setSubmitError] = useState<string | null>(null);

  // 渲染层填充 default：只影响本次渲染与后续 onChange 产出，不原地改调用方 value
  const effective = useMemo(
    () => fillDefaults(schema as SchemaField | undefined, (value ?? {}) as Record<string, unknown>),
    [schema, value],
  );

  // 整个 schema 降级：未知 type / 缺失 properties
  if (!isObjectSchema(schema)) {
    const title =
      schema && typeof schema === 'object' ? ((schema as SchemaField).title ?? '配置') : '配置';
    const description =
      schema && typeof schema === 'object' ? (schema as SchemaField).description : undefined;
    return (
      <div className="flex flex-col gap-2" data-testid="schema-renderer-degraded">
        {schema && typeof schema === 'object' && (schema as SchemaField).title && (
          <h3 className="text-base font-semibold text-[var(--text-primary)]">
            {(schema as SchemaField).title}
          </h3>
        )}
        <p className="text-xs text-[var(--text-muted)]">
          无法识别的 Schema（缺少 properties 或 type 不是 object），已降级为 JSON 编辑。
        </p>
        <JsonFallbackField
          value={value}
          onChange={(next) => onChange((next ?? {}) as Record<string, any>)}
          label={title}
          description={description}
        />
      </div>
    );
  }

  const handleSubmit = () => {
    const issues = validateSchema(schema, effective);
    if (issues.length > 0) {
      setSubmitError(issues.join('；'));
      return;
    }
    setSubmitError(null);
    onSubmit?.(effective as Record<string, any>);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="schema-renderer">
      {schema.title && (
        <h3 className="text-base font-semibold text-[var(--text-primary)]">{schema.title}</h3>
      )}
      {schema.description && (
        <p className="text-sm text-[var(--text-muted)]">{schema.description}</p>
      )}
      <SchemaFields
        properties={schema.properties}
        required={schema.required}
        value={effective}
        onChange={onChange}
        depth={1}
      />
      {submitError && (
        <p role="alert" className="text-sm text-[var(--mascot-error)]">
          {submitError}
        </p>
      )}
      {onSubmit && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={handleSubmit}
            className="rounded-md bg-[var(--mascot-primary)] px-4 py-1.5 text-sm font-medium text-[var(--bg-deep)] transition-colors hover:bg-[var(--mascot-secondary)]"
          >
            提交
          </button>
        </div>
      )}
    </div>
  );
}
