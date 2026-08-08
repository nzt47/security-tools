/**
 * 卡片状态角标（任务6 Step 2）
 *
 * 四种状态配色（draft/current/archive/unknown），unknown 兜底任何未知状态。
 */
import React from 'react';
import { cn } from '../../lib/utils';

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  current: '现行',
  archive: '已归档',
  unknown: '未知',
};

export interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, size = 'sm', className }) => {
  const key = STATUS_LABELS[status] ? status : 'unknown';
  return (
    <span
      className={cn('kb-status-badge', `kb-status-${key}`, `kb-status-${size}`, className)}
      title={`状态: ${status}`}
      data-testid="status-badge"
    >
      {STATUS_LABELS[key]}
    </span>
  );
};

export default StatusBadge;
