import React from 'react';
import './StatusIndicator.css';

export type StatusType = 'online' | 'busy' | 'away' | 'offline' | 'error';

export interface StatusIndicatorProps {
  /** 状态类型 */
  status: StatusType;
  /** 是否显示脉冲动画 */
  pulse?: boolean;
  /** 尺寸大小 */
  size?: 'small' | 'medium' | 'large';
  /** 自定义类名 */
  className?: string;
}

/**
 * 状态指示器组件
 */
const StatusIndicator: React.FC<StatusIndicatorProps> = ({
  status,
  pulse = false,
  size = 'medium',
  className = '',
}) => {
  const sizeClass = `status-size-${size}`;
  const pulseClass = pulse || status === 'online' ? 'status-pulsing' : '';

  return (
    <div className={`status-indicator ${sizeClass} ${pulseClass} ${className}`}>
      <div className={`status-dot ${status}`}>
        {status === 'online' && <div className="status-ring" />}
      </div>
      <span className="status-label">{getStatusLabel(status)}</span>
    </div>
  );
};

const getStatusLabel = (status: StatusType): string => {
  const labels: Record<StatusType, string> = {
    online: '在线',
    busy: '忙碌',
    away: '离开',
    offline: '离线',
    error: '异常',
  };
  return labels[status];
};

export default StatusIndicator;
