import React, { useState, useEffect, useRef } from 'react';
import { Mascot, MascotMood } from './components/Mascot';
import { ChatWindow, Message } from './components/Chat';
import { StatusIndicator } from './components/Status';
import './styles/theme.css';
import './App.css';

/**
 * 存储配置类型
 */
type StorageType = 'localStorage' | 'sessionStorage';

/**
 * 存储适配器接口
 */
interface StorageAdapter {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

/**
 * LocalStorage 适配器
 */
const localStorageAdapter: StorageAdapter = {
  get: (key) => localStorage.getItem(key),
  set: (key, value) => localStorage.setItem(key, value),
  remove: (key) => localStorage.removeItem(key),
};

/**
 * SessionStorage 适配器
 */
const sessionStorageAdapter: StorageAdapter = {
  get: (key) => sessionStorage.getItem(key),
  set: (key, value) => sessionStorage.setItem(key, value),
  remove: (key) => sessionStorage.removeItem(key),
};

/**
 * 存储配置 - 可以轻松切换存储方式
 * 支持两种配置方式：
 * 1. 代码配置：修改 STORAGE_CONFIG.type
 * 2. URL参数：?storage=localStorage 或 ?storage=sessionStorage
 */
const getInitialStorageType = (): StorageType => {
  // 优先从 URL 参数读取
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    const storageParam = params.get('storage');
    if (storageParam === 'localStorage' || storageParam === 'sessionStorage') {
      console.log(`🌐 URL参数指定存储模式: ${storageParam}`);
      return storageParam;
    }
  }
  // 默认使用 localStorage
  return 'localStorage';
};

const STORAGE_CONFIG = {
  type: getInitialStorageType(), // ← 优先使用 URL 参数，否则使用代码配置
};

const getStorageAdapter = (): StorageAdapter => {
  return STORAGE_CONFIG.type === 'sessionStorage'
    ? sessionStorageAdapter
    : localStorageAdapter;
};

const STORAGE_KEY = 'Yunshu_mood';
const MESSAGES_KEY = 'Yunshu_messages';

const getStoredMood = (): MascotMood => {
  try {
    const storage = getStorageAdapter();
    const stored = storage.get(STORAGE_KEY);
    if (stored && ['idle', 'happy', 'excited', 'calm', 'tired', 'thinking', 'error'].includes(stored)) {
      return stored as MascotMood;
    }
  } catch (e) {
    console.warn(`无法读取${STORAGE_CONFIG.type}:`, e);
  }
  return 'idle';
};

const saveMoodToStorage = (mood: MascotMood) => {
  try {
    const storage = getStorageAdapter();
    storage.set(STORAGE_KEY, mood);
  } catch (e) {
    console.warn(`无法保存到${STORAGE_CONFIG.type}:`, e);
  }
};

const getStoredMessages = (): Message[] => {
  try {
    const storage = getStorageAdapter();
    const stored = storage.get(MESSAGES_KEY);
    if (stored) {
      const messages = JSON.parse(stored) as Message[];
      return messages.map(msg => ({
        ...msg,
        timestamp: new Date(msg.timestamp),
      }));
    }
  } catch (e) {
    console.warn('无法读取消息历史:', e);
  }
  return [];
};

const saveMessagesToStorage = (messages: Message[]) => {
  try {
    const storage = getStorageAdapter();
    storage.set(MESSAGES_KEY, JSON.stringify(messages));
  } catch (e) {
    console.warn('无法保存消息历史:', e);
  }
};

const clearAllStorage = () => {
  const storage = getStorageAdapter();
  storage.remove(STORAGE_KEY);
  storage.remove(MESSAGES_KEY);
};

const TestApp: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>(getStoredMessages);
  const [inputValue, setInputValue] = useState('');
  const [mood, setMood] = useState<MascotMood>(getStoredMood);
  const [isTestRunning, setIsTestRunning] = useState(false);
  const [testLog, setTestLog] = useState<string[]>([]);
  const [storageType, setStorageType] = useState<StorageType>(STORAGE_CONFIG.type);
  const testIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const mouseMoveRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  const handleStorageTypeChange = (newType: StorageType) => {
    setStorageType(newType);
    STORAGE_CONFIG.type = newType;
    clearAllStorage();
    setMessages([]);
    setMood('idle');
    setTestLog([]);
    addLog(`存储方式已切换: ${newType}，数据已清空`);
  };

  const moodNames: Record<MascotMood, string> = {
    idle: '待机',
    happy: '开心',
    excited: '兴奋',
    calm: '平静',
    tired: '疲惫',
    thinking: '思考',
    error: '异常',
  };

  const getMoodName = (m: MascotMood): string => moodNames[m] || m;

  const addLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    setTestLog((prev) => [...prev, `[${timestamp}] ${message}`]);
  };

  // 监听情绪变化，保存到localStorage
  useEffect(() => {
    saveMoodToStorage(mood);
  }, [mood]);

  // 监听消息变化，保存到localStorage
  useEffect(() => {
    saveMessagesToStorage(messages);
  }, [messages]);

  const handleSendMessage = (message: string) => {
    const newUserMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: message,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, newUserMessage]);
    addLog(`用户发送: "${message}"`);
    setInputValue('');
    setMood('thinking');
    addLog('情绪变化: idle → thinking');

    setTimeout(() => {
      const responses = [
        '好的，让我来帮你分析一下这个问题。',
        '这是个很好的观点！我有一些想法。',
        '我理解你的意思了。让我详细解释。',
        '根据我的分析，这个情况是这样的...',
      ];
      const randomResponse = responses[Math.floor(Math.random() * responses.length)];

      const newAssistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: randomResponse,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, newAssistantMessage]);
      setMood('happy');
      addLog(`助手回复: "${randomResponse}"`);
      addLog('情绪变化: thinking → happy');

      setTimeout(() => {
        setMood('idle');
        addLog('情绪变化: happy → 待机');
      }, 2000);
    }, 1500);
  };

  const simulateMouseMovement = () => {
    const container = document.querySelector('.mascot-wrapper') as HTMLElement;
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    const movements = [
      { x: centerX + 100, y: centerY, name: '右' },
      { x: centerX - 100, y: centerY, name: '左' },
      { x: centerX, y: centerY - 80, name: '上' },
      { x: centerX, y: centerY + 80, name: '下' },
      { x: centerX + 70, y: centerY - 50, name: '右上' },
      { x: centerX - 70, y: centerY + 50, name: '左下' },
    ];

    let index = 0;
    const moveInterval = setInterval(() => {
      const movement = movements[index];
      mouseMoveRef.current = { x: movement.x, y: movement.y };

      const mouseEvent = new MouseEvent('mousemove', {
        clientX: movement.x,
        clientY: movement.y,
        bubbles: true,
      });
      document.dispatchEvent(mouseEvent);

      addLog(`鼠标移动到${movement.name}: (${Math.round(movement.x)}, ${Math.round(movement.y)})`);
      index = (index + 1) % movements.length;
    }, 800);

    return moveInterval;
  };

  const runAutomatedTest = () => {
    if (isTestRunning) return;

    setIsTestRunning(true);
    addLog('========== 开始自动化测试 ==========');
    setMessages([]);
    setMood('idle');

    const testScenarios = [
      { delay: 0, action: 'reset', message: '重置状态' },
      { delay: 500, action: 'send', message: '你好，云枢！' },
      { delay: 2500, action: 'send', message: '你能帮我解释一下什么是人工智能吗？' },
      { delay: 4500, action: 'send', message: '太棒了！' },
      { delay: 6500, action: 'mood', message: 'tired' },
      { delay: 8500, action: 'mood', message: 'thinking' },
      { delay: 10500, action: 'mood', message: 'excited' },
      { delay: 12500, action: 'stop', message: '测试完成' },
    ];

    const timers: NodeJS.Timeout[] = [];

    testScenarios.forEach((scenario) => {
      const timer = setTimeout(() => {
        switch (scenario.action) {
          case 'reset':
            addLog('重置测试环境');
            break;
          case 'send':
            handleSendMessage(scenario.message);
            break;
          case 'mood':
            setMood(scenario.message as MascotMood);
            addLog(`情绪变化: → ${scenario.message === 'tired' ? '疲惫' : scenario.message === 'thinking' ? '思考' : scenario.message === 'excited' ? '兴奋' : scenario.message}`);
            break;
          case 'stop':
            setIsTestRunning(false);
            addLog('========== 测试完成 ==========');
            break;
        }
      }, scenario.delay);
      timers.push(timer);
    });

    const moveInterval = simulateMouseMovement();
    timers.push(moveInterval as unknown as NodeJS.Timeout);

    setTimeout(() => {
      timers.forEach((timer) => clearTimeout(timer));
      if (moveInterval) clearInterval(moveInterval);
    }, 15000);
  };

  useEffect(() => {
    return () => {
      if (testIntervalRef.current) {
        clearInterval(testIntervalRef.current);
      }
    };
  }, []);

  return (
    <div className="app">
      <div className="app-container">
        {/* 侧边栏 - Mascot 测试区 */}
        <aside className="sidebar">
          <div className="sidebar-header">
            <h1 className="app-title">云枢测试</h1>
            <StatusIndicator status={isTestRunning ? 'busy' : 'online'} size="small" />
          </div>

          <div className="mascot-wrapper">
            <Mascot
              initialMood={mood}
              tracking
              glow
              breathing
              size="large"
              onMoodChange={(newMood) => addLog(`Mood changed: ${newMood}`)}
            />
          </div>

          <div className="mascot-info">
            <p className="mascot-greeting">当前情绪: {getMoodName(mood)}</p>
            <p className="mascot-status">
              {isTestRunning ? '测试运行中...' : '等待测试'}
            </p>
          </div>

          <div className="sidebar-actions">
            <div className="storage-toggle">
              <span className="toggle-label">存储方式:</span>
              <div className="toggle-buttons">
                <button
                  className={`toggle-btn ${storageType === 'localStorage' ? 'active' : ''}`}
                  onClick={() => handleStorageTypeChange('localStorage')}
                  disabled={isTestRunning}
                >
                  永久
                </button>
                <button
                  className={`toggle-btn ${storageType === 'sessionStorage' ? 'active' : ''}`}
                  onClick={() => handleStorageTypeChange('sessionStorage')}
                  disabled={isTestRunning}
                >
                  会话
                </button>
              </div>
            </div>

            <button
              className="action-btn primary"
              onClick={runAutomatedTest}
              disabled={isTestRunning}
            >
              {isTestRunning ? '测试中...' : '运行自动化测试'}
            </button>
            <button
              className="action-btn"
              onClick={() => {
                setMessages([]);
                setMood('idle');
                setTestLog([]);
                clearAllStorage();
                addLog('已重置所有状态');
              }}
            >
              重置
            </button>
          </div>

          {/* 测试日志 */}
          <div className="test-log">
            <h3>测试日志</h3>
            <div className="log-content">
              {testLog.map((log, index) => (
                <p key={index} className="log-entry">
                  {log}
                </p>
              ))}
            </div>
          </div>
        </aside>

        {/* 主内容区 - 聊天窗口 */}
        <main className="main-content">
          <ChatWindow
            messages={messages}
            onSendMessage={handleSendMessage}
            inputValue={inputValue}
            onInputChange={setInputValue}
            disabled={isTestRunning}
            placeholder="输入消息或点击自动化测试..."
          />
        </main>
      </div>
    </div>
  );
};

export default TestApp;
