/**
 * Schema 驱动表单渲染器汇总导出（任务 T3.2）。
 *
 * 用法：
 *   import { SchemaRenderer } from '@/plugins/schema';
 *   import { SelectField, NumberField } from '@/plugins/schema/fields';
 */
export { SchemaRenderer } from './SchemaRenderer';
export type { SchemaRendererProps } from './SchemaRenderer';
export { fillDefaults, isObjectSchema, validateSchema } from './SchemaRenderer';
export { SelectField } from './fields/SelectField';
export type { SelectFieldProps } from './fields/SelectField';
export { InputField } from './fields/InputField';
export type { InputFieldProps } from './fields/InputField';
export { TextareaField } from './fields/TextareaField';
export type { TextareaFieldProps } from './fields/TextareaField';
export { NumberField } from './fields/NumberField';
export type { NumberFieldProps } from './fields/NumberField';
export { SwitchField } from './fields/SwitchField';
export type { SwitchFieldProps } from './fields/SwitchField';
export { TagsField } from './fields/TagsField';
export type { TagsFieldProps } from './fields/TagsField';
export { ObjectGroup } from './fields/ObjectGroup';
export type { ObjectGroupProps } from './fields/ObjectGroup';
export { JsonFallbackField } from './fields/JsonFallbackField';
export type { JsonFallbackFieldProps } from './fields/JsonFallbackField';
export type { SchemaField, SchemaObject, SchemaValueType } from './types';
