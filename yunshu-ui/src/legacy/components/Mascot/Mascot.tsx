import React, { useState, useEffect, useCallback, useRef } from 'react';
import Eye, { EyeMood } from './Eye';
import Glow, { GlowColor } from './Glow';
import './animations.css';
import './Mascot.css';

export type MascotMood = 'happy' | 'excited' | 'calm' | 'tired' | 'thinking' | 'error' | 'idle';

export interface MascotProps {
  /** 初始情绪状态 */
  initialMood?: MascotMood;
  /** 是否启用注视追踪 */
  tracking?: boolean;
  /** 是否显示光晕 */
  glow?: boolean;
  /** 是否启用呼吸动画 */
  breathing?: boolean;
  /** 尺寸大小 */
  size?: 'small' | 'medium' | 'large';
  /** 自定义类名 */
  className?: string;
  /** 情绪变化回调 */
  onMoodChange?: (mood: MascotMood) => void;
  /** 点击回调 */
  onClick?: () => void;
  /** 是否启用调试日志 */
  debug?: boolean;
  /** 追踪灵敏度 (0.1-1.0) */
  sensitivity?: number;
  /** 平滑阻尼系数 (0.1-0.3) */
  damping?: number;
  /** 是否启用鼠标停止回弹 */
  enableReturnToCenter?: boolean;
  /** 回弹延迟时间（毫秒） */
  returnDelay?: number;
  /** 回弹阻尼系数 */
  returnDamping?: number;
}

/**
 * 云枢 Mascot 组件
 * 数字生命的视觉核心
 */
const Mascot: React.FC<MascotProps> = ({
  initialMood = 'idle',
  tracking = false,
  glow = true,
  breathing = true,
  size = 'medium',
  className = '',
  onMoodChange,
  onClick,
  debug = false,
  sensitivity = 0.5,
  damping = 0.2,
  enableReturnToCenter = true,
  returnDelay = 3000,
  returnDamping = 0.08,
}) => {
  const [mood, setMood] = useState<MascotMood>(initialMood);
  const [lookAt, setLookAt] = useState({ x: 0, y: 0 });
  const [isHovered, setIsHovered] = useState(false);
  const mascotRef = useRef<HTMLDivElement>(null);
  const targetLookAtRef = useRef({ x: 0, y: 0 });
  const animationRef = useRef<number | null>(null);
  const lastMoveTimeRef = useRef<number>(Date.now());
  const returnTimerRef = useRef<number | null>(null);
  const isReturningRef = useRef(false);

  // 鼠标追踪 - 优化：以组件自身位置为参考点，带平滑过渡
  useEffect(() => {
    if (!tracking) return;

    const handleMouseMove = (e: MouseEvent) => {
      // 更新最后移动时间
      lastMoveTimeRef.current = Date.now();
      isReturningRef.current = false;

      // 清除回弹定时器
      if (returnTimerRef.current) {
        clearTimeout(returnTimerRef.current);
        returnTimerRef.current = null;
      }

      const mascotRect = mascotRef.current?.getBoundingClientRect();
      if (!mascotRect) {
        if (debug) console.warn('[Mascot] 组件尚未挂载，无法获取位置');
        return;
      }
      
      // 计算组件中心点坐标
      const mascotCenterX = mascotRect.left + mascotRect.width / 2;
      const mascotCenterY = mascotRect.top + mascotRect.height / 2;

      // 计算鼠标相对于组件中心的偏移
      const viewportHalfWidth = window.innerWidth / 2;
      const viewportHalfHeight = window.innerHeight / 2;
      
      const deltaX = (e.clientX - mascotCenterX) / viewportHalfWidth;
      const deltaY = (e.clientY - mascotCenterY) / viewportHalfHeight;

      // 限制偏移范围并乘以灵敏度系数
      const clampedX = Math.max(-1, Math.min(1, deltaX * sensitivity));
      const clampedY = Math.max(-1, Math.min(1, deltaY * sensitivity));

      // 更新目标位置（不直接更新显示值）
      targetLookAtRef.current = { x: clampedX, y: clampedY };

      // 调试日志
      if (debug) {
        console.debug('[Mascot 视线追踪]', {
          mouseX: e.clientX,
          mouseY: e.clientY,
          mascotCenterX: Math.round(mascotCenterX),
          mascotCenterY: Math.round(mascotCenterY),
          deltaX: deltaX.toFixed(3),
          deltaY: deltaY.toFixed(3),
          targetX: clampedX.toFixed(3),
          targetY: clampedY.toFixed(3),
        });
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, [tracking, debug, sensitivity]);

  // 鼠标停止后回弹到中心
  useEffect(() => {
    if (!tracking || !enableReturnToCenter) return;

    const checkReturnToCenter = () => {
      const now = Date.now();
      const timeSinceLastMove = now - lastMoveTimeRef.current;

      // 如果鼠标停止移动超过指定时间，且当前位置不在中心附近，则开始回弹
      if (timeSinceLastMove >= returnDelay && !isReturningRef.current) {
        const currentTarget = targetLookAtRef.current;
        const distance = Math.sqrt(currentTarget.x ** 2 + currentTarget.y ** 2);
        
        if (distance > 0.01) {
          isReturningRef.current = true;
          if (debug) console.debug('[Mascot] 鼠标停止移动，开始回弹到中心');
        }
      }

      // 回弹过程中，逐步将目标位置移向中心
      if (isReturningRef.current) {
        targetLookAtRef.current = {
          x: targetLookAtRef.current.x * (1 - returnDamping),
          y: targetLookAtRef.current.y * (1 - returnDamping),
        };

        // 检查是否已经回到中心
        const distance = Math.sqrt(
          targetLookAtRef.current.x ** 2 + targetLookAtRef.current.y ** 2
        );
        if (distance < 0.005) {
          targetLookAtRef.current = { x: 0, y: 0 };
          isReturningRef.current = false;
          if (debug) console.debug('[Mascot] 已回到中心位置');
        }
      }

      requestAnimationFrame(checkReturnToCenter);
    };

    const intervalId = requestAnimationFrame(checkReturnToCenter);
    return () => cancelAnimationFrame(intervalId);
  }, [tracking, enableReturnToCenter, returnDelay, returnDamping, debug]);

  // 平滑过渡动画 - 使用 requestAnimationFrame 实现阻尼效果
  useEffect(() => {
    if (!tracking) return;

    const animate = () => {
      setLookAt(prev => {
        const target = targetLookAtRef.current;
        
        // 计算当前值与目标值的差值
        const diffX = target.x - prev.x;
        const diffY = target.y - prev.y;
        
        // 使用阻尼系数进行平滑插值
        const newX = prev.x + diffX * damping;
        const newY = prev.y + diffY * damping;
        
        // 调试日志 - 打印插值后的实际坐标值
        if (debug) {
          console.debug('[Mascot 插值更新]', {
            prevX: prev.x.toFixed(4),
            prevY: prev.y.toFixed(4),
            targetX: target.x.toFixed(4),
            targetY: target.y.toFixed(4),
            newX: newX.toFixed(4),
            newY: newY.toFixed(4),
            diffX: diffX.toFixed(4),
            diffY: diffY.toFixed(4),
          });
        }
        
        // 当差值很小时直接设置为目标值，避免抖动
        const threshold = 0.001;
        if (Math.abs(diffX) < threshold && Math.abs(diffY) < threshold) {
          return target;
        }
        
        return { x: newX, y: newY };
      });
      
      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);
    
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [tracking, damping, debug]);

  // 情绪变化
  const changeMood = useCallback((newMood: MascotMood) => {
    setMood(newMood);
    onMoodChange?.(newMood);
  }, [onMoodChange]);

  // 情绪状态映射
  const getEyeMood = (): EyeMood => {
    switch (mood) {
      case 'happy':
        return 'happy';
      case 'excited':
        return 'surprised';
      case 'thinking':
        return 'thinking';
      case 'tired':
        return 'tired';
      case 'error':
        return 'error';
      case 'calm':
        return 'normal';
      default:
        return 'normal';
    }
  };

  const getGlowColor = (): GlowColor => {
    switch (mood) {
      case 'happy':
        return 'happy';
      case 'excited':
        return 'excited';
      case 'tired':
        return 'tired';
      case 'thinking':
        return 'thinking';
      case 'error':
        return 'error';
      default:
        return 'primary';
    }
  };

  const getSize = (): 'small' | 'medium' | 'large' => {
    return size;
  };

  return (
    <div
      ref={mascotRef}
      className={`mascot-container ${breathing ? 'mascot-breathing' : ''} mascot-mood-${mood} ${className}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onClick}
    >
      {glow && (
        <Glow
          size={getSize()}
          color={getGlowColor()}
          pulsing={mood === 'excited' || mood === 'thinking'}
        />
      )}

      <div className={`mascot-body ${isHovered ? 'mascot-hovered' : ''}`}>
        <div className="mascot-face">
          <div className="mascot-eyes">
            <Eye
              size={size}
              mood={getEyeMood()}
              lookAt={lookAt}
              tracking={tracking}
              className="mascot-eye-left"
            />
            <Eye
              size={size}
              mood={getEyeMood()}
              lookAt={lookAt}
              tracking={tracking}
              className="mascot-eye-right"
            />
          </div>

          {mood === 'excited' && (
            <div className="mascot-mouth mascot-mouth-excited">◡</div>
          )}

          {mood === 'happy' && (
            <div className="mascot-mouth mascot-mouth-happy">◡</div>
          )}

          {mood === 'thinking' && (
            <div className="mascot-mouth mascot-mouth-thinking">•</div>
          )}

          {mood === 'tired' && (
            <div className="mascot-mouth mascot-mouth-tired">—</div>
          )}

          {mood === 'error' && (
            <div className="mascot-mouth mascot-mouth-error">✕</div>
          )}
        </div>
      </div>

      <div className="mascot-shadow" />
    </div>
  );
};

export default Mascot;
