/**
 * 状态角标（任务6）：draft / current / archive / unknown 四种配色。
 * 渲染纯展示，无交互；通过 data-status 属性供 CSS 定制。
 */
import React from 'react';
import type { CardStatus } from '../../api/knowledge-types';
import './StatusBadge.css';

/** 状态显示文案映射（与 lifecycle.py 命名一致） */
const STATUS_TEXT: Record<CardStatus, string> = {
  draft: '草稿',
  current: '有效',
  archive: '归档',
  unknown: '未知',
};

interface StatusBadgeProps {
  status: CardStatus;
  /** 是否显示文字（默认显示） */
  withText?: boolean;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, withText = true }) => (
  <span className={`kb-status-badge kb-status--${status}`} data-status={status} title={`状态: ${STATUS_TEXT[status] ?? status}`}>
    {withText && (STATUS_TEXT[status] ?? status)}
  </span>
);

export default StatusBadge;
