/**
 * PromptLab —— 提示词影响因素管理面板（主页编排）
 * ------------------------------------------------
 * 布局：顶栏（标题 + 模块 Tab 条）+ 【影响因素 Tab：① 基础模板（默认收起）→ 分类筛选
 *      → ② 分节配置 / 因素区 / ③ 最终注入文本】/ LLM 通信监控 Tab（全宽）/ 人格 Tab
 * 功能：6 类因素调节（滑块/下拉/文本/开关）、实时预览（真实模型试跑 + Token/雷达/导出）、
 *       分类筛选、自定义因素添加/删除、JSON/CSV 导出。
 *
 * 【2026-11-15 三层合并（§11.124 D-1）】原先「系统提示词」是独立 Tab，与实验室左栏
 * 「身份提示词」、右栏「最终注入文本」三层同名、各在一处，看着像三份重复的东西。
 * 现在系统提示词 Tab 取消，三层按 ① 基础模板（骨架 + 占位符，默认收起）→ ② 分节配置
 * → ③ 最终注入文本 排在同一屏里顺着读。存量用户存过 'system-prompt' Tab 的，
 * readSavedTab() 会把它归一到 'factors'（否则刷新后落在一个不存在的 Tab 上）。
 *
 * 【2026-11-15 人格并进因素（§11.124 D-2 轻量版）】人格 6 维从独立 Tab 复制一份到因素里
 * （Tab 保留）：滑块直接写 /api/personality（防抖 400ms，见 personalityFactors.ts）。
 *
 * 【深度合并（身份提示词 + LLM 通信并入）】
 *   - 「系统提示词」区不再本地沙箱：由 useIdentityPrompt 直接编辑后端
 *     「身份提示词」线上配置（启停/自定义内容/保存/重置），预览与
 *     「请求真实输出」注入的 system message 由后端模板引擎生成。
 *   - 原页面**最底部**的「LLM 通信监控」已提到**主内容区**，与「提示词影响因素
 *     实验室」并列为一个 Tab（req：从最后位置移至主内容区，TAB 形式并列展示）。
 *   - 顶栏不再提供「返回工作台」按钮（工作台内导航由左侧导航树负责）。
 *
 * 子组件拆分见 prompt-lab/ 目录：RadarChart / FactorControl / FactorCard /
 * CustomFactorForm / PreviewPanel / IdentityPromptPanel / LlmMonitorPanel。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { FileCode2, FlaskConical, Plus, Radio, RotateCcw, Smile, TriangleAlert } from 'lucide-react';
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import { downloadFile } from '../../utils/system';
import {
  CATEGORIES,
  allFactors,
  buildPrompt,
  exportCsv,
  exportJson,
  factorsOfCategory,
  numOf,
  radarData,
  requestLlmPreview,
  requestBackendLlmPreview,
  usageReport,
} from '../../lib/promptFactors';
import type { FactorCategory, FactorValue } from '../../lib/promptFactorTypes';
import FactorCard from './FactorCard';
import CustomFactorForm from './CustomFactorForm';
import PreviewPanel from './PreviewPanel';
import IdentityPromptPanel, { toIdentityPromptPanelProps } from './IdentityPromptPanel';
import LlmMonitorPanel from './LlmMonitorPanel';
// 2026-10-18：系统提示词模板（3 条 /api/system-prompt*）并入本页 Tab ——
// 它与"影响因素/人格"同属"模型侧配置"，此前只有后端与手改文件。
// 2026-11-15：不再独占 Tab，改由影响因素页的「① 基础模板」（默认收起）承载
import SystemPromptPanel from './SystemPromptPanel';
// 人格配置（原「人格与提示词 → 人格配置」独立页）并入本页第 3 个 Tab（阶段 1.1）
import PersonalityPage from '../hub/personality';
import { useIdentityPrompt } from './identityPrompt';
// 人格 6 维 ↔ /api/personality 的同步（首屏 GET 覆盖 + 防抖 400ms POST）
import { usePersonalityFactors } from './personalityFactors';
import '../PromptLab.css';

type Filter = 'all' | FactorCategory;

/** 主内容区模块 Tab（并列展示）：影响因素实验室 / LLM 通信监控 / 人格 */
type LabTab = 'factors' | 'llm-monitor' | 'personality';

const LAB_TABS: { id: LabTab; label: string; icon: typeof FlaskConical; hint: string }[] = [
  { id: 'factors', label: '提示词影响因素实验室', icon: FlaskConical, hint: '① 基础模板 → ② 分节配置 → ③ 最终注入文本，外加 6 类因素（含人格）与试跑' },
  { id: 'llm-monitor', label: 'LLM 通信监控', icon: Radio, hint: '每次 LLM 调用的完整收发内容（折叠展开）+ Token 统计' },
  // 阶段 1.1：原「人格与提示词 → 人格配置」独立页并入本页 Tab。
  // 语义：人格是"它是谁"（画像），因素实验室是"它怎么答"（行为），同属"模型侧配置"。
  // 2026-11-15：这 6 维同时进了「影响因素 → 人格」分类（调了直接写后端），此 Tab 保留为
  // 「预设人格 + 重置」的整体视图，两者共用同一份 /api/personality。
  { id: 'personality', label: '人格', icon: Smile, hint: '云枢的语气体征（语气/情感/简练/主动/幽默/同理心）+ 预设人格' },
];

/** 记住上次所在 Tab（会话级偏好，刷新不跳回） */
const TAB_STORAGE_KEY = 'yunshu.prompt-lab.tab';

const readSavedTab = (): LabTab => {
  try {
    const v = localStorage.getItem(TAB_STORAGE_KEY);
    // 存量值归一（2026-11-15 三层合并）：'system-prompt' 这个 Tab 已经不存在了，
    // 老用户浏览器里还存着它 —— 不归一就会刷新后落在一个没有对应分支的 Tab 上（空白页）。
    if (v === 'system-prompt') return 'factors';
    return LAB_TABS.some((t) => t.id === v) ? (v as LabTab) : 'factors';
  } catch {
    return 'factors';
  }
};

const dateTag = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

export default function PromptLab() {
  const values = usePromptLabStore((s) => s.values);
  const customFactors = usePromptLabStore((s) => s.customFactors);
  const llm = usePromptLabStore((s) => s.llm);
  const setValue = usePromptLabStore((s) => s.setValue);
  const removeCustomFactor = usePromptLabStore((s) => s.removeCustomFactor);
  const resetValues = usePromptLabStore((s) => s.resetValues);

  // 深度合并：线上「身份提示词」配置（后端模板 = 注入 system message 的唯一来源）
  const identity = useIdentityPrompt();
  // 人格 6 维：首屏 GET 覆盖默认值，拖动后防抖 400ms 写回 /api/personality
  const setValues = usePromptLabStore((s) => s.setValues);
  const personality = usePersonalityFactors(values, setValues);
  /** ② 分节配置列（人格黄条上的「去分节配置」滚到这里并高亮 persona 节） */
  const identityColRef = useRef<HTMLElement | null>(null);

  const [tab, setTab] = useState<LabTab>(readSavedTab);
  const [filter, setFilter] = useState<Filter>('all');
  const [showForm, setShowForm] = useState(false);
  // 身份提示词面板聚焦项：悬停某面板项 → 右侧提示词区域高亮该节的发出内容
  const [focusRowKey, setFocusRowKey] = useState<string | null>(null);
  const [llmOutput, setLlmOutput] = useState<string | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(TAB_STORAGE_KEY, tab);
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab]);

  const defs = useMemo(() => allFactors(customFactors), [customFactors]);

  const prompt = useMemo(() => buildPrompt(values), [values]);
  const radar = useMemo(() => radarData(values), [values]);
  const systemPrompt = identity.template;
  // 人格那 6 维参数是否真的进了提示词：由身份提示词的 persona 节说了算（见人格 Tab 的提示条）
  const personaEmitted = identity.rows.some((r) => r.key === 'persona' && r.emitted);
  const token = useMemo(
    () => usageReport(systemPrompt, prompt, llm.contextWindow),
    [systemPrompt, prompt, llm.contextWindow],
  );

  const shownCategories = useMemo(
    () => (filter === 'all' ? CATEGORIES : CATEGORIES.filter((c) => c.id === filter)),
    [filter],
  );

  /** 人格黄条上的「去分节配置」：滚到 ② 分节配置列并高亮 persona 节（同一屏内，无需切 Tab） */
  const focusPersonaRow = () => {
    setFocusRowKey('persona');
    // jsdom 里没有 scrollIntoView（用例会渲染本页）⇒ 可选调用，不写死依赖
    identityColRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };

  /** 人格 6 维的后端同步状态（一行小字，如实说现在是"后端值"还是"还没读到"） */
  const personalityStatus = personality.loading
    ? '正在读取后端人格参数…'
    : personality.error
      ? personality.error
      : personality.saving
        ? '正在写入 /api/personality…'
        : personality.syncedAt !== null
          ? '已写入 /api/personality（后端档位 custom）'
          : `已载入后端参数（档位 ${personality.profile || '未知'}）· 拖动后自动写回，停手 400ms 发一次`;

  const runLlm = async () => {
    if (!llm.useBackend && !llm.endpoint.trim()) {
      setLlmError('先勾选「用云枢默认 LLM」（推荐），或在「高级：改用自己填的接口」里填 Endpoint。');
      return;
    }
    setLlmLoading(true);
    setLlmError(null);
    // 【2026-10-14 修 404】默认走后端代理（用 .env 已配置的 LLM），
    // 只有用户显式取消勾选时才直连自带的 OpenAI 兼容地址。
    const res = llm.useBackend
      ? await requestBackendLlmPreview({
          systemPrompt,
          prompt,
          model: llm.model,
          maxTokens: numOf(values, 'max_tokens'),
        })
      : await requestLlmPreview({
          endpoint: llm.endpoint.trim(),
          apiKey: llm.apiKey,
          model: llm.model,
          systemPrompt,
          prompt,
          temperature: numOf(values, 'temperature'),
          topP: numOf(values, 'top_p'),
          maxTokens: numOf(values, 'max_tokens'),
        });
    setLlmLoading(false);
    if (res.ok === true) {
      setLlmOutput(res.text);
      setLlmError(null);
    } else {
      setLlmError(res.error);
    }
  };

  const onValueChange = (id: string, v: FactorValue) => {
    setValue(id, v);
    setLlmOutput(null); // 参数变化后旧的模型回答失效（避免拿旧答案对照新参数）
  };

  const exportWith = (kind: 'json' | 'csv') => {
    const tag = dateTag();
    if (kind === 'json')
      downloadFile(`prompt-factors-${tag}.json`, exportJson(values, customFactors, systemPrompt, llm.contextWindow), 'application/json');
    else downloadFile(`prompt-factors-${tag}.csv`, exportCsv(values, customFactors), 'text/csv;charset=utf-8');
  };

  const activeTab = LAB_TABS.find((t) => t.id === tab) ?? LAB_TABS[0];
  const ActiveTabIcon = activeTab.icon;

  return (
    <div className="pl-root">
      {/* 顶栏 */}
      <header className="pl-topbar">
        <div className="flex items-center gap-2.5">
          <div className="pl-logo-badge">
            <ActiveTabIcon size={15} />
          </div>
          <div>
            <h1 className="pl-title">提示词实验室</h1>
            <p className="pl-subtitle">{activeTab.hint}</p>
          </div>
        </div>
        <div className="pl-topbar-actions">
          {tab === 'factors' && (
            <>
              <button type="button" className="pl-btn" onClick={() => setShowForm(true)}>
                <Plus size={13} />
                添加因素
              </button>
              <button type="button" className="pl-btn" onClick={resetValues} title="所有因素恢复默认值">
                <RotateCcw size={13} />
                重置
              </button>
            </>
          )}
        </div>
      </header>

      {/* 主内容区 Tab 条：影响因素实验室 / LLM 通信监控 并列展示 */}
      <nav className="pl-tabs" role="tablist" aria-label="提示词实验室模块">
        {LAB_TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`pl-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
              title={t.hint}
            >
              <Icon size={13} />
              {t.label}
            </button>
          );
        })}
      </nav>

      {tab === 'factors' ? (
        <>
          {/* ① 基础模板（三层合并的第一层）：骨架 + 占位符。
              **默认收起** —— 它是一次性配置（改骨架的机会很少），日常只调因素/分节；
              而且这三层原先各占一个 Tab，第一眼看到一整块编辑器正是"像重复"的来源。 */}
          <details className="pl-base-block">
            <summary>
              <FileCode2 size={13} />
              ① 基础模板（骨架 + 占位符，约 150 tok）
              <span className="pl-base-hint">下面 ② 决定哪些块真的拼进去；拼好的全文见 ③</span>
            </summary>
            <div className="pl-base-block-body">
              <SystemPromptPanel />
            </div>
          </details>

          {/* 分类筛选 */}
          <nav className="pl-filter" aria-label="因素分类筛选">
            {(['all', ...CATEGORIES.map((c) => c.id)] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                className={`pl-filter-chip ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? '全部' : CATEGORIES.find((c) => c.id === f)?.short}
              </button>
            ))}
          </nav>

          <div className="pl-body">
            {/* 第 1 列：因素（分类筛选在上方）。
                2026-11-15 Owner：「上面那个『全部』（不是按钮）字样也删了」—— 那一行是列标题，
                分类标题本来就自明（还有筛选条），它只是多占一行。 */}
            <main className="pl-factors">
              {shownCategories.map((cat) => (
                <section key={cat.id} className="pl-category">
                  <h2 className="pl-category-title" style={{ color: cat.color }}>
                    <span className="pl-category-dot" style={{ background: cat.color }} />
                    {cat.label}
                    <span className="pl-category-count">
                      {factorsOfCategory(defs, cat.id).length} 项
                    </span>
                  </h2>
                  <p className="pl-category-desc">{cat.desc}</p>
                  {/* 人格分类：视图如实（§11.123 K-2）—— 调了写后端，但进不进提示词由 persona 节决定 */}
                  {cat.id === 'personality' && (
                    <>
                      {!personaEmitted && (
                        // 这里在 300px 窄列里，故用 pl-warn-strip-narrow（可换行、按钮独占一行），
                        // 文案也比「人格」Tab 上的短 —— ② 分节配置就在同一屏右边，不必长篇解释。
                        <div className="pl-warn-strip pl-warn-strip-narrow" role="status">
                          <TriangleAlert size={13} />
                          <span>
                            <strong>现在调不会进提示词</strong>：persona 节是关的（约 1500 tok），
                            拖动只写后端 <code>/api/personality</code>。
                          </span>
                          <button type="button" className="pl-btn" onClick={focusPersonaRow}>
                            去 ② 分节配置打开
                          </button>
                        </div>
                      )}
                      <p className="pl-personality-status">{personalityStatus}</p>
                    </>
                  )}
                  <div className="pl-card-grid">
                    {factorsOfCategory(defs, cat.id).map((def) => (
                      <FactorCard
                        key={def.id}
                        def={def}
                        value={values[def.id] ?? def.defaultValue}
                        onChange={(v) => onValueChange(def.id, v)}
                        onRemove={removeCustomFactor}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </main>

            {/* 第 2 列：② 分节配置（哪些块进提示词）= 原「身份提示词（系统提示词 · 线上配置）」 */}
            <section className="pl-identity-col" aria-label="身份提示词（系统提示词 · 线上配置）" ref={identityColRef}>
              <h2 className="pl-col-title">② 分节配置（哪些块进提示词）</h2>
              <IdentityPromptPanel
                {...toIdentityPromptPanelProps(identity)}
                focusedKey={focusRowKey}
                onFocusRow={setFocusRowKey}
              />
            </section>

            {/* 第 3 列：实时预览面板（PreviewPanel） */}
            <PreviewPanel
              systemPrompt={systemPrompt}
              highlightSnippet={focusRowKey ? identity.rows.find((r) => r.key === focusRowKey)?.emitText : null}
              prompt={prompt}
              radar={radar}
              token={token}
              llmOutput={llmOutput}
              llmError={llmError}
              llmLoading={llmLoading}
              onRunLlm={() => void runLlm()}
              onExport={exportWith}
            />
          </div>
        </>
      ) : tab === 'llm-monitor' ? (
        /* LLM 通信监控：主内容区整屏（原页面底部并入 → 提为主 Tab） */
        <div className="pl-tab-body">
          <LlmMonitorPanel />
        </div>
      ) : (
        /* 人格：原「人格与提示词 → 人格配置」独立页并入（阶段 1.1） */
        <div className="pl-tab-body">
          {/* 视图如实（2026-11-15）：人格参数走 /api/personality，但**进不进提示词**由
              系统提示词的 persona 节决定；该节默认关闭 ⇒ 面板上怎么调都不会改变回答。
              不写这条提示，这里就是个静默空转的页面（Owner 问「人格能不能并进因素」时发现的）。 */}
          {!personaEmitted && (
            <div className="pl-warn-strip" role="status">
              <TriangleAlert size={13} />
              <span>
                这里的 6 维参数目前
                <strong>不会进入提示词</strong>：系统提示词的「persona 节」是关闭的（约 1500 tok）。
                要让它们真的影响回答，需要在「提示词影响因素实验室 → ② 分节配置」里开启 persona 节
                （这 6 维在那边也有一份，两边改的是同一份后端参数）。
              </span>
              <button type="button" className="pl-btn" onClick={() => setTab('factors')}>
                去分节配置
              </button>
            </div>
          )}
          <PersonalityPage />
        </div>
      )}

      {showForm && <CustomFactorForm onClose={() => setShowForm(false)} />}
    </div>
  );
}
