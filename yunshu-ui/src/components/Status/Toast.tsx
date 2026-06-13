import React, { useEffect, useState } from 'react';
import './Toast.css';

export type ToastType = 'info' | 'success' | 'warning' | 'error';

export interface ToastData {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

export interface ToastProps {
  toast: ToastData;
  onClose: (id: string) => void;
}

/**
 * Toast 通知组件
 */
const Toast: React.FC<ToastProps> = ({ toast, onClose }) => {
  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    const duration = toast.duration || 5000;
    const timer = setTimeout(() => {
      setIsExiting(true);
      setTimeout(() => onClose(toast.id), 300);
    }, duration);

    return () => clearTimeout(timer);
  }, [toast, onClose]);

  const handleClose = () => {
    setIsExiting(true);
    setTimeout(() => onClose(toast.id), 300);
  };

  return (
    <div className={`toast ${toast.type} ${isExiting ? 'exiting' : ''}`}>
      <div className="toast-icon">{getIcon(toast.type)}</div>
      <div className="toast-content">
        <p className="toast-message">{toast.message}</p>
      </div>
      <button className="toast-close" onClick={handleClose}>
        ×
      </button>
      <div className="toast-progress" />
    </div>
  );
};

const getIcon = (type: ToastType): React.ReactNode => {
  switch (type) {
    case 'success':
      return <span className="toast-icon-success">✓</span>;
    case 'warning':
      return <span className="toast-icon-warning">⚠</span>;
    case 'error':
      return <span className="toast-icon-error">✕</span>;
    default:
      return <span className="toast-icon-info">ℹ</span>;
  }
};

export interface ToastContainerProps {
  toasts: ToastData[];
  onClose: (id: string) => void;
}

/**
 * Toast 容器组件
 */
export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onClose }) => {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <Toast key={toast.id} toast={toast} onClose={onClose} />
      ))}
    </div>
  );
};

export default Toast;
