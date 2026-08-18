/**
 * 技能管理 & 工作流学习 — Zustand 状态管理
 *
 * 状态同步机制说明（遵循"前后端状态绝对同步"原则）：
 * - Request ID：searchSkills 使用自增 requestId，仅最新请求的响应会更新列表，杜绝竞态
 * - AbortController：每次搜索/加载都创建新 controller，新请求发起时取消上一个未完成请求
 * - 乐观更新回滚：toggle / remove / update 使用 try/catch + 闭包缓存旧状态，失败时精准回滚
 * - 后端权威原则：写操作成功后用后端返回的 skill 替换本地副本，而非前端自行推导
 * - 防连点：所有写操作通过 set loading 标志位阻止重复触发
 */

import { create } from 'zustand';
import {
  skillsApi,
  workflowApi,
  SkillsApiError,
  type Skill,
  type SkillSearchParams,
  type LearnedWorkflow,
  type ReviewResult,
  type SkillVersion,
} from '../lib/skillsApi';

interface SkillsState {
  // ─── 数据 ───
  skills: Skill[];
  total: number;
  page: number;
  pageSize: number;
  selectedSkill: Skill | null;
  selectedVersions: SkillVersion[];
  workflows: LearnedWorkflow[];

  // ─── 加载状态 ───
  loadingList: boolean;
  loadingDetail: boolean;
  loadingVersions: boolean;
  loadingWorkflows: boolean;
  submitting: boolean;  // 写操作统一 loading（防连点）
  error: string | null;
  detailError: string | null;

  // ─── 健康状态 ───
  skillsHealth: 'unknown' | 'online' | 'offline';
  workflowHealth: 'unknown' | 'online' | 'offline';

  // ─── 内部追踪（不暴露给组件）───
  _searchReqId: number;
  _searchAbort: AbortController | null;
  _listAbort: AbortController | null;
  _workflowsAbort: AbortController | null;

  // ─── 动作 ───
  searchSkills: (params: SkillSearchParams) => Promise<void>;
  loadAllSkills: () => Promise<void>;
  selectSkill: (skillId: string | null) => Promise<void>;
  loadVersions: (skillId: string) => Promise<void>;
  createAi: (payload: { name: string; intent: string; category?: Skill['category']; tags?: string[] }) => Promise<Skill>;
  createManual: (payload: Partial<Skill> & { name: string; content: string }) => Promise<Skill>;
  install: (source: string, force?: boolean) => Promise<Skill>;
  reviewSkill: (skillId: string) => Promise<ReviewResult>;
  reviewAllPending: () => Promise<number>;
  updateSkill: (skillId: string, patch: Partial<Skill>) => Promise<void>;
  deleteSkill: (skillId: string) => Promise<void>;
  toggleSkill: (skillId: string, enabled: boolean) => Promise<void>;
  bumpVersion: (skillId: string, kind: 'major' | 'minor' | 'patch', changelog?: string, content?: string) => Promise<void>;
  rollbackVersion: (skillId: string, targetVersion: string) => Promise<void>;
  optimizeSkill: (skillId: string) => Promise<string[]>;
  recordExecution: (skillId: string, success: boolean, latencyMs: number) => Promise<void>;

  // ─── 工作流动作 ───
  loadWorkflows: (enabledOnly?: boolean) => Promise<void>;
  toggleWorkflow: (wfId: string, enabled: boolean) => Promise<void>;
  deleteWorkflow: (wfId: string) => Promise<void>;
  setWorkflowPriority: (wfId: string, priority: number) => Promise<void>;
  matchWorkflows: (taskText: string, topK?: number) => Promise<{ workflow: LearnedWorkflow; similarity: number; score: number }[]>;
  tryExecuteWorkflow: (taskText: string, params?: Record<string, unknown>) => Promise<{ skipped: boolean; result?: unknown; reason?: string }>;

  // ─── 健康检查 ───
  checkHealth: () => Promise<void>;

  // ─── 错误清理 ───
  clearError: () => void;
  clearDetailError: () => void;
}

export const useSkillsStore = create<SkillsState>((set, get) => ({
  // ─── 初始数据 ───
  skills: [],
  total: 0,
  page: 1,
  pageSize: 20,
  selectedSkill: null,
  selectedVersions: [],
  workflows: [],
  loadingList: false,
  loadingDetail: false,
  loadingVersions: false,
  loadingWorkflows: false,
  submitting: false,
  error: null,
  detailError: null,
  skillsHealth: 'unknown',
  workflowHealth: 'unknown',
  _searchReqId: 0,
  _searchAbort: null,
  _listAbort: null,
  _workflowsAbort: null,

  // ═══════════════════════════════════════════════════════════
  //  搜索：Request ID + AbortController 双重防竞态
  // ═══════════════════════════════════════════════════════════
  searchSkills: async (params) => {
    // 取消上一个未完成的搜索请求
    const prevAbort = get()._searchAbort;
    if (prevAbort) prevAbort.abort();

    const controller = new AbortController();
    const reqId = get()._searchReqId + 1;
    set({ _searchAbort: controller, _searchReqId: reqId, loadingList: true, error: null });

    try {
      const res = await skillsApi.search(params, controller.signal);
      // 仅最新请求的响应才允许更新 UI
      if (get()._searchReqId !== reqId) return;
      set({
        skills: res.items,
        total: res.total,
        page: res.page,
        pageSize: res.page_size,
        loadingList: false,
      });
    } catch (e) {
      if (get()._searchReqId !== reqId) return;
      // AbortError 静默处理（用户主动取消或新请求替代）
      if (e instanceof SkillsApiError && e.code === 'SKILL_REQUEST_ABORTED') {
        set({ loadingList: false });
        return;
      }
      set({
        loadingList: false,
        error: e instanceof Error ? e.message : '搜索失败',
      });
    }
  },

  loadAllSkills: async () => {
    const prev = get()._listAbort;
    if (prev) prev.abort();
    const controller = new AbortController();
    set({ _listAbort: controller, loadingList: true, error: null });
    try {
      const res = await skillsApi.list(controller.signal);
      set({ skills: res.items, total: res.items.length, loadingList: false });
    } catch (e) {
      if (e instanceof SkillsApiError && e.code === 'SKILL_REQUEST_ABORTED') {
        set({ loadingList: false });
        return;
      }
      set({ loadingList: false, error: e instanceof Error ? e.message : '加载失败' });
    }
  },

  // ═══════════════════════════════════════════════════════════
  //  选中技能详情：AbortController
  // ═══════════════════════════════════════════════════════════
  selectSkill: async (skillId) => {
    if (skillId === null) {
      set({ selectedSkill: null, selectedVersions: [] });
      return;
    }
    set({ loadingDetail: true, detailError: null });
    try {
      const res = await skillsApi.get(skillId);
      set({ selectedSkill: res.skill, loadingDetail: false });
    } catch (e) {
      set({
        loadingDetail: false,
        detailError: e instanceof Error ? e.message : '加载详情失败',
      });
    }
  },

  loadVersions: async (skillId) => {
    set({ loadingVersions: true });
    try {
      const res = await skillsApi.listVersions(skillId);
      set({ selectedVersions: res.versions, loadingVersions: false });
    } catch (e) {
      set({ loadingVersions: false });
      // 版本加载失败不阻塞主流程
      console.warn('[skillsStore] 加载版本失败:', e);
    }
  },

  // ═══════════════════════════════════════════════════════════
  //  写操作：submitting 标志位防连点 + 后端权威原则
  // ═══════════════════════════════════════════════════════════
  createAi: async (payload) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.createAi(payload);
      // 后端权威：用返回的 skill 写入列表头部
      set((s) => ({
        skills: [res.skill, ...s.skills],
        total: s.total + 1,
        submitting: false,
      }));
      return res.skill;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : 'AI 生成失败' });
      throw e;
    }
  },

  createManual: async (payload) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.createManual(payload);
      set((s) => ({ skills: [res.skill, ...s.skills], total: s.total + 1, submitting: false }));
      return res.skill;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '创建失败' });
      throw e;
    }
  },

  install: async (source, force) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.install({ source, force });
      set((s) => ({ skills: [res.skill, ...s.skills], total: s.total + 1, submitting: false }));
      return res.skill;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '安装失败' });
      throw e;
    }
  },

  reviewSkill: async (skillId) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.review(skillId);
      // 后端权威：用返回的 skill + review 替换本地副本
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
      return res.review;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '审核失败' });
      throw e;
    }
  },

  reviewAllPending: async () => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.reviewBatch();
      // 后端权威：重新加载列表
      await get().loadAllSkills();
      set({ submitting: false });
      return res.reviewed;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '批量审核失败' });
      throw e;
    }
  },

  // ═══════════════════════════════════════════════════════════
  //  乐观更新回滚：toggle / update / delete
  // ═══════════════════════════════════════════════════════════
  updateSkill: async (skillId, patch) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    // 闭包缓存旧状态，用于失败时精准回滚
    const prevSkills = get().skills;
    const prevSelected = get().selectedSkill;
    // 乐观更新
    set((s) => ({
      skills: s.skills.map((sk) => (sk.id === skillId ? { ...sk, ...patch } : sk)),
      selectedSkill: s.selectedSkill?.id === skillId
        ? { ...s.selectedSkill, ...patch }
        : s.selectedSkill,
      submitting: true,
      error: null,
    }));
    try {
      const res = await skillsApi.update(skillId, patch);
      // 后端权威：用返回值替换
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
    } catch (e) {
      // 失败 → 精准回滚到旧状态
      set({
        skills: prevSkills,
        selectedSkill: prevSelected,
        submitting: false,
        error: e instanceof Error ? e.message : '更新失败',
      });
      throw e;
    }
  },

  deleteSkill: async (skillId) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    const prevSkills = get().skills;
    const prevSelected = get().selectedSkill;
    // 乐观删除
    set((s) => ({
      skills: s.skills.filter((sk) => sk.id !== skillId),
      selectedSkill: s.selectedSkill?.id === skillId ? null : s.selectedSkill,
      submitting: true,
      error: null,
    }));
    try {
      await skillsApi.remove(skillId);
      set((s) => ({ total: Math.max(0, s.total - 1), submitting: false }));
    } catch (e) {
      // 失败 → 回滚
      set({
        skills: prevSkills,
        selectedSkill: prevSelected,
        submitting: false,
        error: e instanceof Error ? e.message : '删除失败',
      });
      throw e;
    }
  },

  toggleSkill: async (skillId, enabled) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    const prevSkills = get().skills;
    const prevSelected = get().selectedSkill;
    // 乐观更新
    set((s) => ({
      skills: s.skills.map((sk) => (sk.id === skillId ? { ...sk, enabled } : sk)),
      selectedSkill: s.selectedSkill?.id === skillId
        ? { ...s.selectedSkill, enabled }
        : s.selectedSkill,
      submitting: true,
      error: null,
    }));
    try {
      const res = await skillsApi.toggle(skillId, enabled);
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
    } catch (e) {
      set({
        skills: prevSkills,
        selectedSkill: prevSelected,
        submitting: false,
        error: e instanceof Error ? e.message : '切换失败',
      });
      throw e;
    }
  },

  bumpVersion: async (skillId, kind, changelog, content) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.bumpVersion(skillId, kind, changelog, content);
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
      // 重新加载版本列表
      await get().loadVersions(skillId);
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '版本升级失败' });
      throw e;
    }
  },

  rollbackVersion: async (skillId, targetVersion) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.rollbackVersion(skillId, targetVersion);
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
      await get().loadVersions(skillId);
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '版本回滚失败' });
      throw e;
    }
  },

  optimizeSkill: async (skillId) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await skillsApi.optimize(skillId);
      set((s) => ({
        skills: s.skills.map((sk) => (sk.id === skillId ? res.skill : sk)),
        selectedSkill: s.selectedSkill?.id === skillId ? res.skill : s.selectedSkill,
        submitting: false,
      }));
      return res.suggestions;
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '优化失败' });
      throw e;
    }
  },

  recordExecution: async (skillId, success, latencyMs) => {
    // 不设置 submitting（这是次要操作，不阻塞 UI）
    try {
      await skillsApi.recordExecution(skillId, success, latencyMs);
    } catch (e) {
      console.warn('[skillsStore] 记录执行失败:', e);
    }
  },

  // ═══════════════════════════════════════════════════════════
  //  工作流学习
  // ═══════════════════════════════════════════════════════════
  loadWorkflows: async (enabledOnly) => {
    const prev = get()._workflowsAbort;
    if (prev) prev.abort();
    set({ loadingWorkflows: true });
    try {
      const res = await workflowApi.list(enabledOnly);
      set({ workflows: res.workflows, loadingWorkflows: false });
    } catch (e) {
      set({ loadingWorkflows: false });
      console.warn('[skillsStore] 加载工作流失败:', e);
    }
  },

  toggleWorkflow: async (wfId, enabled) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    const prev = get().workflows;
    set((s) => ({
      workflows: s.workflows.map((w) => (w.id === wfId ? { ...w, enabled } : w)),
      submitting: true,
      error: null,
    }));
    try {
      const res = await workflowApi.toggle(wfId, enabled);
      set((s) => ({
        workflows: s.workflows.map((w) => (w.id === wfId ? res.workflow : w)),
        submitting: false,
      }));
    } catch (e) {
      set({ workflows: prev, submitting: false, error: e instanceof Error ? e.message : '切换失败' });
      throw e;
    }
  },

  deleteWorkflow: async (wfId) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    const prev = get().workflows;
    set((s) => ({
      workflows: s.workflows.filter((w) => w.id !== wfId),
      submitting: true,
      error: null,
    }));
    try {
      await workflowApi.remove(wfId);
      set({ submitting: false });
    } catch (e) {
      set({ workflows: prev, submitting: false, error: e instanceof Error ? e.message : '删除失败' });
      throw e;
    }
  },

  setWorkflowPriority: async (wfId, priority) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    const prev = get().workflows;
    set((s) => ({
      workflows: s.workflows.map((w) => (w.id === wfId ? { ...w, priority } : w)),
      submitting: true,
      error: null,
    }));
    try {
      const res = await workflowApi.setPriority(wfId, priority);
      set((s) => ({
        workflows: s.workflows.map((w) => (w.id === wfId ? res.workflow : w)),
        submitting: false,
      }));
    } catch (e) {
      set({ workflows: prev, submitting: false, error: e instanceof Error ? e.message : '调整优先级失败' });
      throw e;
    }
  },

  matchWorkflows: async (taskText, topK) => {
    try {
      const res = await workflowApi.match(taskText, topK);
      return res.matches;
    } catch (e) {
      console.warn('[skillsStore] 匹配工作流失败:', e);
      return [];
    }
  },

  tryExecuteWorkflow: async (taskText, params) => {
    if (get().submitting) throw new Error('正在处理中，请稍后');
    set({ submitting: true, error: null });
    try {
      const res = await workflowApi.tryExecute(taskText, params);
      set({ submitting: false });
      // 执行后刷新工作流列表（统计已变化）
      await get().loadWorkflows(false);
      return { skipped: res.skipped, result: res.result, reason: res.reason };
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '执行失败' });
      throw e;
    }
  },

  // ═══════════════════════════════════════════════════════════
  //  健康检查
  // ═══════════════════════════════════════════════════════════
  checkHealth: async () => {
    try {
      await skillsApi.checkHealth();
      set({ skillsHealth: 'online' });
    } catch {
      set({ skillsHealth: 'offline' });
    }
    try {
      await workflowApi.checkHealth();
      set({ workflowHealth: 'online' });
    } catch {
      set({ workflowHealth: 'offline' });
    }
  },

  clearError: () => set({ error: null }),
  clearDetailError: () => set({ detailError: null }),
}));
