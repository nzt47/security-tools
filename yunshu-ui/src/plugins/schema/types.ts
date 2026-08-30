/**
 * Schema 驱动表单的 Schema 子集类型（任务 T3.2，协议见 docs/yunshu-pluginization/PLAN-3-schema-ui.md §2）。
 *
 * 前端只承诺实现以下关键字；子集之外的关键字不阻塞渲染：
 * - 未知 type / 缺失 properties 的字段降级为 JSON 编辑（JsonFallbackField）；
 * - 未知顶层 type / 缺失顶层 properties → 整个 schema 降级。
 */

/** 协议承诺的值类型（前端可渲染的控件范围） */
export type SchemaValueType =
  | 'string'
  | 'integer'
  | 'number'
  | 'boolean'
  | 'array'
  | 'object';

/**
 * 单个字段定义（JSON Schema 子集）。
 * 允许携带未知关键字（元信息透传，不影响渲染）。
 */
export interface SchemaField {
  /** 值类型；未知类型 → JsonFallbackField 降级 */
  type?: SchemaValueType | string;
  /** 字段标题（渲染为 label） */
  title?: string;
  /** 字段说明 */
  description?: string;
  /** 默认值（渲染时填充缺失字段） */
  default?: unknown;
  /** 可选值列表（string + enum → SelectField） */
  enum?: unknown[];
  /** 数值下限（integer/number） */
  minimum?: number;
  /** 数值上限（integer/number） */
  maximum?: number;
  /** array 元素定义（array of string → TagsField） */
  items?: SchemaField;
  /** 格式化提示（string + format:'textarea' → TextareaField） */
  format?: string;
  /** 嵌套对象属性表（type:'object' → ObjectGroup 折叠分组） */
  properties?: Record<string, SchemaField>;
  /** 必填字段名列表（对象级） */
  required?: string[];
  /** 子集外关键字透传 */
  [key: string]: unknown;
}

/** 顶层 schema 等价于一个 object 字段（properties 存在性由调用方保证，缺失时整体降级） */
export interface SchemaObject extends SchemaField {
  type?: 'object' | SchemaValueType | string;
  properties?: Record<string, SchemaField>;
}
