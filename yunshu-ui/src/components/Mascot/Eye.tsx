import React, { useEffect, useRef, useState } from 'react';
import './Eye.css';

export type EyeMood = 'happy' | 'surprised' | 'thinking' | 'tired' | 'alert' | 'error' | 'normal';

export interface EyeProps {
  /** 眼睛大小 */
  size?: 'small' | 'medium' | 'large';
  /** 情绪状态 */
  mood?: EyeMood;
  /** 注视方向 */
  lookAt?: { x: number; y: number };
  /** 是否眨眼中 */
  blinking?: boolean;
  /** 是否启用注视追踪 */
  tracking?: boolean;
  /** 自定义类名 */
  className?: string;
  /** 回调函数 */
  onBlink?: () => void;
}

const Eye: React.FC<EyeProps> = ({
  size = 'medium',
  mood = 'normal',
  lookAt = { x: 0, y: 0 },
  blinking = false,
  tracking = false,
  className = '',
  onBlink,
}) => {
  const [blinkState, setBlinkState] = useState(false);
  const [currentLook, setCurrentLook] = useState({ x: 0, y: 0 });
  const eyeRef = useRef<HTMLDivElement>(null);
  const pupilRef = useRef<HTMLDivElement>(null);

  // 随机眨眼
  useEffect(() => {
    const randomBlink = () => {
      const nextBlink = 3000 + Math.random() * 4000; // 3-7秒
      setTimeout(() => {
        setBlinkState(true);
        onBlink?.();
        setTimeout(() => setBlinkState(false), 150);
        randomBlink();
      }, nextBlink);
    };
    randomBlink();
  }, [onBlink]);

  // 注视追踪
  useEffect(() => {
    if (!tracking) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!eyeRef.current) return;

      const rect = eyeRef.current.getBoundingClientRect();
      const eyeCenterX = rect.left + rect.width / 2;
      const eyeCenterY = rect.top + rect.height / 2;

      const deltaX = (e.clientX - eyeCenterX) / window.innerWidth;
      const deltaY = (e.clientY - eyeCenterY) / window.innerHeight;

      const maxOffset = 3;
      setCurrentLook({
        x: Math.max(-maxOffset, Math.min(maxOffset, deltaX * maxOffset * 2)),
        y: Math.max(-maxOffset, Math.min(maxOffset, deltaY * maxOffset * 2)),
      });
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, [tracking]);

  // 应用注视偏移
  useEffect(() => {
    if (pupilRef.current) {
      const maxOffset = 3;
      pupilRef.current.style.transform = `translate(calc(-50% + ${currentLook.x}px), calc(-50% + ${currentLook.y}px))`;
    }
  }, [currentLook]);

  const sizeClass = `eye-size-${size}`;
  const moodClass = `eye-mood-${mood}`;
  const blinkClass = blinkState || blinking ? 'eye-blinking' : '';

  return (
    <div
      ref={eyeRef}
      className={`eye ${sizeClass} ${moodClass} ${blinkClass} ${className}`}
    >
      <div ref={pupilRef} className="eye-pupil" />
      <div className="eye-highlight" />
    </div>
  );
};

export default Eye;
