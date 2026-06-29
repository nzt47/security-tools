/**
 * 可观测性统一浮层入口
 *
 * 组合 DevConsole（网络/错误/性能）与 StateInspector（状态快照/时间线），
 * 通过 DevConsole 的 extraTabs 注入"状态"Tab。
 *
 * 状态同步机制说明：
 * - 安装拦截器在 useEffect 中完成，组件卸载时自动卸载拦截器，避免内存泄漏；
 * - 生产环境由 main.tsx 的条件动态 import 保证本模块不被打包（tree-shaking）。
 */

import React, { useEffect, useRef } from 'react';
import { DevConsole } from '@/components/DevConsole';
import type { TabDescriptor } from '@/components/DevConsole';
import { useDevConsoleStore } from '@/components/DevConsole';
import { StateInspector, useStateInspectorStore } from '@/components/StateInspector';

const ObservabilityDevtools: React.FC = () => {
  const install = useDevConsoleStore((s) => s.install);
  const snapshotCount = useStateInspectorStore((s) => s.snapshots.size);
  const timelineCount = useStateInspectorStore((s) => s.timeline.length);
  const uninstallRef = useRef<() => void>(() => {});

  // 安装请求拦截器（fetch / XHR / 错误捕获）
  useEffect(() => {
    uninstallRef.current = install();
    return () => {
      uninstallRef.current();
    };
  }, [install]);

  // 注入"状态"Tab：展示 StateInspector（内部含快照/时间线切换）
  const extraTabs: TabDescriptor[] = [
    {
      key: 'state',
      label: '状态',
      count: snapshotCount + timelineCount,
      render: () => <StateInspector />,
    },
  ];

  return <DevConsole extraTabs={extraTabs} />;
};

export default ObservabilityDevtools;
