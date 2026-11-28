/**
 * 思考过程翻译客户端（E3-4）：POST /api/context/translate。
 *
 * 【为什么单开一个 api 文件】HistoryDrawer / 将来的思考面板都要用；
 * 放这里可被 route_ui_coverage 的前端字面量扫描识别（endpoints.ts 里已有常量）。
 */
import { request } from './apiClient';
import { CONTEXT_TRANSLATE } from '@/api/endpoints';

export interface CotTranslateResult {
  ok?: boolean;
  code?: string;
  error?: string;
  text?: string;
  target?: string;
  cached?: boolean;
}

export async function translateCot(
  text: string,
  target: 'zh' | 'en' = 'zh',
): Promise<CotTranslateResult> {
  return request<CotTranslateResult>(CONTEXT_TRANSLATE, {
    method: 'POST',
    body: { text, target },
  });
}
