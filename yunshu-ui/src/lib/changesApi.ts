/**
 * 变更文件卡 API（E1-3b）
 * ------------------------------------------------
 * 对应后端 `agent/server_routes/routes_changes.py`；端点字面量在 `src/api/endpoints.ts`。
 * 数据源是 E0-3 的工作区检查点（最近一次，或指定 checkpoint），**只读**。
 */
import { request } from './apiClient';
import { CHANGES } from '@/api/endpoints';

/** 检查点摘要（后端 manifest 的投影） */
export interface ChangeCheckpoint {
  id: string;
  created_at: string;
  trigger_tool: string;
  session_id?: string;
  git_head?: string;
  changed_total?: number;
  files_captured?: number;
  diff_truncated?: boolean;
}

/** 单个文件的变更（diff 可能被截断） */
export interface ChangedFile {
  path: string;
  status: string;
  size: number;
  captured: boolean;
  binary: boolean;
  /** 未跟踪/新增文件：`git diff` 里没有块，内容在检查点里是整文件捕获 */
  new_file: boolean;
  added: number;
  removed: number;
  diff: string;
  truncated: boolean;
}

export interface ChangesState {
  session_id: string;
  /** session = 快照就是这个会话触发的；workspace = 工作区级快照（可能含别的动作） */
  scope: 'session' | 'workspace';
  checkpoint: ChangeCheckpoint | null;
  files: ChangedFile[];
  counts: { files: number; added: number; removed: number; truncated: number };
  note?: string;
}

/** 读最近一次（或指定）检查点的逐文件变更 */
export async function getChanges(
  sessionId: string,
  checkpointId?: string,
): Promise<ChangesState> {
  const query = `?session_id=${encodeURIComponent(sessionId)}${
    checkpointId ? `&checkpoint=${encodeURIComponent(checkpointId)}` : ''
  }`;
  return request<ChangesState>(`${CHANGES}${query}`);
}
