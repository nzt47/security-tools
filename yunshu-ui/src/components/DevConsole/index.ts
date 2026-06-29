/**
 * DevConsole 模块导出
 *
 * 仅导出必要 API，内部实现细节不外露。
 */

export { default as DevConsole } from './DevConsole';
export { useDevConsoleStore } from './store';
export { trackPerf, measureRender } from './store';
export type {
  NetworkRecord,
  ErrorRecord,
  PerfRecord,
  DevConsoleTab,
} from './types';
export type { TabDescriptor } from './DevConsole';
