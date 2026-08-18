import React from 'react';
import './Glow.css';

export type GlowColor = 'primary' | 'happy' | 'excited' | 'calm' | 'tired' | 'thinking' | 'error';

export interface GlowProps {
  /** 光晕大小 */
  size?: 'small' | 'medium' | 'large';
  /** 光晕颜色 */
  color?: GlowColor;
  /** 是否启用脉冲动画 */
  pulsing?: boolean;
  /** 自定义类名 */
  className?: string;
  /** 子元素 */
  children?: React.ReactNode;
}

const Glow: React.FC<GlowProps> = ({
  size = 'medium',
  color = 'primary',
  pulsing = false,
  className = '',
  children,
}) => {
  const sizeClass = `glow-size-${size}`;
  const colorClass = `glow-color-${color}`;
  const pulseClass = pulsing ? 'glow-pulsing' : '';

  return (
    <div className={`glow ${sizeClass} ${colorClass} ${pulseClass} ${className}`}>
      <div className="glow-inner" />
      <div className="glow-middle" />
      <div className="glow-outer" />
      {children && <div className="glow-content">{children}</div>}
    </div>
  );
};

export default Glow;
