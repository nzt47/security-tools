/**
 * PromptLab —— 提示词影响因素管理面板（主页编排）
 * ------------------------------------------------
 * 布局：顶栏（标题 + 模块 Tab 条）+ 【影响因素 Tab：分类筛选 → 因素列 + 生成的提示词/试跑】
 *      / 身份提示词 Tab（分节配置 + 最终注入文本）/ LLM 通信监控 Tab（全宽）
 * 功能：6 类因素调节（滑块/下拉/文本/开关）、实时预览（真实模型试跑 + Token/雷达/导出）、
 *       分类筛选、自定义因素添加/删除、JSON/CSV 导出。
 *
 * 【2026-11-15 拆 Tab】Owner：「把 身份提示词 及身份提示词变化产生的实时预览拆成一个 TAB」
 * ⇒ 影响因素（滑块 + 生成的提示词/试跑）与 身份提示词（分节配置 + 最终注入文本）互不干扰，
 * 各自一屏，各自可拖列宽。
 *
 * 【2026-11-15 两层收口】原先「系统提示词」是独立 Tab，与实验室左栏「身份提示词」、
 * 右栏「最终注入文本」三层同名、各在一处，看着像三份重复的东西。先改成三层同屏
 * （基础模板折叠块），Owner 又定：「基础模板可以不要，分节配置的开关本身就是在组装基础
 * 模板，只要每一个开关修改都可以在实时预览中看到就可以了」⇒ **基础模板编辑器整个删掉**
 * （SystemPromptPanel 与 /api/system-prompt 的三个端点常量一并删），
 * 页面只剩「分节配置 → 最终注入文本」两层（现各自在身份提示词 Tab 里）。存量用户存过 'system-prompt' Tab 的，
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
import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { FileText, FlaskConical, Plus, Radio, RotateCcw, TriangleAlert } from 'lucide-react';
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
// 【2026-11-15 人格不再有两份】原「人格」Tab（pages/hub/personality.tsx）已删除，
// 6 维滑块 + 预设 + 重置全部住在影响因素页的「人格」分类里（单一入口）。
import { useIdentityPrompt } from './identityPrompt';
// 人格 6 维 ↔ /api/personality 的同步（首屏 GET 覆盖 + 防抖 400ms POST）
import { usePersonalityFactors } from './personalityFactors';
// 三列宽度可拖拽 + 自动保存（Owner 2026-11-15：「由我来定列宽吧」）
import { useColumnLayout } from './columnLayout';
import '../PromptLab.css';

type Filter = 'all' | FactorCategory;

/**
 * 主内容区模块 Tab（并列展示）：影响因素实验室 / LLM 通信监控。
 *
 * 【2026-11-15 人格 Tab 删除】Owner：「人格不要两份，合成到轻量版就可以，TAB 的可以删掉。」
 * 人格 6 维 + 预设 + 重置现在只在「影响因素 → 人格」分类里（pages/hub/personality.tsx 已删）。
 */
type LabTab = 'factors' | 'identity' | 'llm-monitor';

const LAB_TABS: { id: LabTab; label: string; icon: typeof FlaskConical; hint: string }[] = [
  { id: 'factors', label: '提示词影响因素实验室', icon: FlaskConical, hint: '6 类因素（含人格与预设）调节 → 生成的提示词实时可见 + 试跑/评估/Token/导出' },
  // 【2026-11-15 Owner】「把 身份提示词 及身份提示词变化产生的 实时预览拆成一个 TAB」
  { id: 'identity', label: '身份提示词', icon: FileText, hint: '分节配置（每个开关就是一节组装）+ 它组装出来的最终注入文本（实时）' },
  { id: 'llm-monitor', label: 'LLM 通信监控', icon: Radio, hint: '每次 LLM 调用的完整收发内容（折叠展开）+ Token 统计' },
];

/** 记住上次所在 Tab（会话级偏好，刷新不跳回） */
const TAB_STORAGE_KEY = 'yunshu.prompt-lab.tab';

const readSavedTab = (): LabTab => {
  try {
    const v = localStorage.getItem(TAB_STORAGE_KEY);
    // 存量值归一（2026-11-15 三层合并）：'system-prompt' 这个 Tab 已经不存在了，
    // 老用户浏览器里还存着它 —— 不归一就会刷新后落在一个没有对应分支的 Tab 上（空白页）。
    if (v === 'system-prompt') return 'factors';
    // 同理：'personality' Tab 已于 2026-11-15 删除（人格并进影响因素），存量值也要归一
    if (v === 'personality') return 'factors';
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

  /** 三列宽度：拖拽条改，松手写 localStorage（Owner：「由我来定列宽吧」） */
  const columns = useColumnLayout();

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
  // 人格那 6 维参数是否真的进了提示词：由身份提示词的 persona 节说了算（见人格分类的黄条）
  const personaEmitted = identity.rows.some((r) => r.key === 'persona' && r.emitted);
  const token = useMemo(
    () => usageReport(systemPrompt, prompt, llm.contextWindow),
    [systemPrompt, prompt, llm.contextWindow],
  );

  const shownCategories = useMemo(
    () => (filter === 'all' ? CATEGORIES : CATEGORIES.filter((c) => c.id === filter)),
    [filter],
  );

  /**
   * 人格黄条上的「去分节配置」：切到「身份提示词」Tab 并高亮 persona 节。
   * 【2026-11-15 拆 Tab 后】分节配置不再与因素同屏，故这里是切 Tab 而不是滚动。
   */
  const focusPersonaRow = () => {
    setFocusRowKey('persona');
    setTab('identity');
  };

  /**
   * 人格 6 维的后端同步状态（一行小字，如实说现在是"后端值"还是"还没读到"）。
   * 【2026-11-15 删除人格 Tab 后】预设/重置也在这里，故状态里要能看出"正在应用预设"。
   */
  const personalityStatus = personality.loading
    ? '正在读取后端人格参数…'
    : personality.error
      ? personality.error
      : personality.applying
        ? '正在应用人格预设…'
        : personality.saving
          ? '正在写入 /api/personality…'
          : personality.syncedAt !== null
            ? `已写入 /api/personality（档位 ${personality.profile || 'custom'}）`
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

          {/* 三列宽度由 Owner 拖定（两条拖拽条，松手即写 localStorage）：
              宽度用 CSS 变量下发给 .pl-factors / .pl-identity-col，正文列仍是 flex:1 吃剩余，
              因此窄屏（≤1300 纵向堆叠）的媒体查询可以直接把它们复位成满宽。 */}
          <div
            className={`pl-body ${columns.dragging ? 'pl-body-dragging' : ''}`}
            ref={columns.bodyRef}
            style={
              {
                '--pl-col-factors': `${columns.cols.factors}px`,
                '--pl-col-identity': `${columns.cols.identity}px`,
              } as CSSProperties
            }
          >
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
                        // 文案也短 —— 分节配置在「身份提示词」Tab 里，点按钮直接过去。
                        <div className="pl-warn-strip pl-warn-strip-narrow" role="status">
                          <TriangleAlert size={13} />
                          <span>
                            <strong>现在调不会进提示词</strong>：persona 节是关的（约 1500 tok），
                            拖动只写后端 <code>/api/personality</code>。
                          </span>
                          <button type="button" className="pl-btn" onClick={focusPersonaRow}>
                            去「身份提示词」打开
                          </button>
                        </div>
                      )}
                      {/* 预设人格 + 重置：原本在「人格」Tab 的 PageHeader 与预设卡里，
                          Tab 删除后搬到这里（能力不能跟着 Tab 一起消失）。 */}
                      {Object.keys(personality.profiles).length > 0 && (
                        <div className="pl-preset-row" role="group" aria-label="人格预设">
                          {Object.entries(personality.profiles).map(([key, preset]) => (
                            <button
                              key={key}
                              type="button"
                              className={`pl-preset-chip ${personality.profile === key ? 'active' : ''}`}
                              onClick={() => personality.applyProfile(key)}
                              disabled={personality.applying}
                              title={preset.description || preset.name}
                              aria-pressed={personality.profile === key}
                            >
                              {preset.name}
                            </button>
                          ))}
                          <button
                            type="button"
                            className="pl-preset-chip"
                            onClick={personality.reset}
                            disabled={personality.applying}
                            title="回到默认档（温和助人型）"
                          >
                            重置
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

            {/* 拖拽条：因素列 ↔ 预览（拖动只改因素列宽；双击复位） */}
            <div
              className="pl-col-split"
              role="separator"
              aria-orientation="vertical"
              aria-label="拖动调整因素列宽"
              title="拖动调整列宽（双击恢复默认）"
              onMouseDown={columns.startDrag('factors')}
              onDoubleClick={columns.reset}
            />

            {/* 右侧：因素滑块的产物（生成的提示词 / 试跑 / 评估 / Token / 导出）。
                「身份提示词组装出来的最终注入文本」已按 Owner 要求挪到独立 Tab。 */}
            <PreviewPanel
              part="factors"
              systemPrompt={systemPrompt}
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
      ) : tab === 'identity' ? (
        /* 【2026-11-15 Owner】「把 身份提示词 及身份提示词变化产生的 实时预览拆成一个 TAB」——
           这个 Tab = 分节配置（开关）+ 它组装出来的最终注入文本（实时）。 */
        <div
          className={`pl-body ${columns.dragging ? 'pl-body-dragging' : ''}`}
          ref={columns.bodyRef}
          style={
            {
              '--pl-col-factors': `${columns.cols.factors}px`,
              '--pl-col-identity': `${columns.cols.identity}px`,
            } as CSSProperties
          }
        >
          <section className="pl-identity-col" aria-label="身份提示词（分节配置）">
            <h2 className="pl-col-title">分节配置（哪些块进提示词）</h2>
            <IdentityPromptPanel
              {...toIdentityPromptPanelProps(identity)}
              focusedKey={focusRowKey}
              onFocusRow={setFocusRowKey}
            />
          </section>

          <div
            className="pl-col-split"
            role="separator"
            aria-orientation="vertical"
            aria-label="拖动调整身份提示词列宽"
            title="拖动调整列宽（双击恢复默认）"
            onMouseDown={columns.startDrag('identity')}
            onDoubleClick={columns.reset}
          />

          <PreviewPanel
            part="identity"
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
      ) : (
        /* LLM 通信监控：主内容区整屏（原页面底部并入 → 提为主 Tab） */
        <div className="pl-tab-body">
          <LlmMonitorPanel />
        </div>
      )}

      {showForm && <CustomFactorForm onClose={() => setShowForm(false)} />}
    </div>
  );
}
