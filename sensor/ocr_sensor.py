"""
OCR 传感器 — 屏幕内容识别
通过屏幕截图 + OCR 识别当前屏幕上的文字内容
我是云枢的"视觉神经"——我能看到屏幕上显示的内容
"""

import logging
import time
import os
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
    pytesseract = None


class OcrSensor:
    """屏幕内容 OCR 识别传感器"""

    def __init__(self, config_path="data/ocr_config.json", save_callback=None):
        """
        初始化 OCR 传感器

        Args:
            config_path: 配置文件路径
            save_callback: 保存结果的回调函数 function(ocr_type, data)
        """
        self._config_path = config_path
        self._save_callback = save_callback
        self._config = self._load_config()
        self._is_capturing = False
        self._last_capture_time = 0
        self._capture_cooldown = self._config.get("capture_cooldown_sec", 5)

    def _load_config(self):
        """加载配置"""
        defaults = {
            "enabled": False,  # 默认禁用
            "capture_cooldown_sec": 5,  # 捕获间隔（秒）
            "max_text_length": 5000,  # 最大文字长度
            "languages": ["eng", "chi_sim"],  # 识别语言
            "capture_region": None,  # 捕获区域，None 表示全屏
        }
        try:
            if os.path.exists(self._config_path):
                import json
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
        except Exception as e:
            logger.warning(f"加载 OCR 配置失败: {e}")
        return defaults

    def save_config(self, new_config):
        """保存配置"""
        self._config.update(new_config)
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            import json
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 OCR 配置失败: {e}")

    def get_config(self):
        """获取配置"""
        return dict(self._config)

    @property
    def is_available(self) -> bool:
        """检查 OCR 是否可用"""
        return HAS_CV2 and HAS_NUMPY and HAS_TESSERACT

    def capture_screen(self, region=None) -> Optional[np.ndarray]:
        """
        捕获屏幕截图

        Args:
            region: 捕获区域 (x, y, width, height)，None 表示全屏

        Returns:
            numpy.ndarray 格式的图像，或 None
        """
        if not HAS_CV2:
            logger.warning("OpenCV 不可用，无法捕获屏幕")
            return None

        try:
            import mss

            with mss.mss() as sct:
                if region:
                    monitor = {
                        "left": region[0],
                        "top": region[1],
                        "width": region[2],
                        "height": region[3]
                    }
                else:
                    monitor = sct.monitors[1]  # 主显示器

                screenshot = sct.grab(monitor)
                img = np.array(screenshot)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                return img

        except Exception as e:
            logger.error(f"屏幕捕获失败: {e}")
            return None

    def preprocess_image(self, img: np.ndarray) -> np.ndarray:
        """
        预处理图像以提高 OCR 准确率

        Args:
            img: 输入图像

        Returns:
            处理后的图像
        """
        if not HAS_CV2:
            return img

        try:
            # 转换为灰度图
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # 去噪声
            denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)

            # 自适应阈值
            thresh = cv2.adaptiveThreshold(
                denoised, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                11, 2
            )

            return thresh

        except Exception as e:
            logger.debug(f"图像预处理失败: {e}")
            return img

    def recognize_text(self, img: np.ndarray, languages: Optional[List[str]] = None) -> str:
        """
        从图像中识别文字

        Args:
            img: 输入图像
            languages: 语言列表，如 ["eng", "chi_sim"]

        Returns:
            识别的文字内容
        """
        if not HAS_TESSERACT:
            return ""

        try:
            langs = languages or self._config.get("languages", ["eng"])
            lang_str = "+".join(langs)

            # 使用 pytesseract 进行 OCR
            text = pytesseract.image_to_string(
                img,
                lang=lang_str,
                config="--psm 6"  # 假设统一文本块
            )

            # 清理文本
            text = self._clean_text(text)

            # 限制长度
            max_len = self._config.get("max_text_length", 5000)
            if len(text) > max_len:
                text = text[:max_len] + "..."

            return text

        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return ""

    def _clean_text(self, text: str) -> str:
        """清理 OCR 识别结果"""
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            if line and len(line) > 1:
                lines.append(line)
        return "\n".join(lines)

    def capture_and_recognize(self, region=None) -> Dict[str, Any]:
        """
        捕获屏幕并识别文字的完整流程

        Returns:
            {
                "timestamp": str,
                "text": str,
                "word_count": int,
                "has_content": bool
            }
        """
        if not self.is_available:
            return {
                "timestamp": "",
                "text": "",
                "word_count": 0,
                "has_content": False,
                "error": "OCR 库不可用"
            }

        # 冷却时间检查
        now = time.time()
        if now - self._last_capture_time < self._capture_cooldown:
            return {
                "timestamp": "",
                "text": "",
                "word_count": 0,
                "has_content": False,
                "error": "冷却中"
            }

        # 捕获屏幕
        img = self.capture_screen(region)
        if img is None:
            return {
                "timestamp": "",
                "text": "",
                "word_count": 0,
                "has_content": False,
                "error": "屏幕捕获失败"
            }

        # 预处理
        processed = self.preprocess_image(img)

        # OCR 识别
        text = self.recognize_text(processed)

        self._last_capture_time = now

        # 构建结果
        result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "text": text,
            "word_count": len(text.split()),
            "has_content": len(text.strip()) > 0,
            "char_count": len(text)
        }

        # 如果有保存回调，调用它
        if self._save_callback and result["has_content"]:
            try:
                self._save_callback("ocr_content", result)
            except Exception as e:
                logger.debug(f"OCR 结果保存失败: {e}")

        return result

    def capture_window(self, hwnd=None) -> Dict[str, Any]:
        """
        捕获指定窗口的内容

        Args:
            hwnd: 窗口句柄，None 表示当前前台窗口

        Returns:
            OCR 结果字典
        """
        if not HAS_CV2 or not HAS_WIN32:
            return {
                "timestamp": "",
                "text": "",
                "word_count": 0,
                "has_content": False,
                "error": "窗口捕获不可用"
            }

        try:
            import win32gui

            # 如果没有指定句柄，获取前台窗口
            if hwnd is None:
                hwnd = win32gui.GetForegroundWindow()

            # 获取窗口位置和大小
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top

            # 捕获窗口内容
            img = self.capture_screen((left, top, width, height))
            if img is None:
                return {
                    "timestamp": "",
                    "text": "",
                    "word_count": 0,
                    "has_content": False,
                    "error": "窗口捕获失败"
                }

            # 预处理和识别
            processed = self.preprocess_image(img)
            text = self.recognize_text(processed)

            # 获取窗口标题
            title = win32gui.GetWindowText(hwnd)

            result = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "window_title": title,
                "text": text,
                "word_count": len(text.split()),
                "has_content": len(text.strip()) > 0,
                "char_count": len(text),
                "region": {"left": left, "top": top, "width": width, "height": height}
            }

            if self._save_callback and result["has_content"]:
                try:
                    self._save_callback("window_content", result)
                except Exception as e:
                    logger.debug(f"窗口 OCR 结果保存失败: {e}")

            return result

        except Exception as e:
            logger.error(f"窗口捕获失败: {e}")
            return {
                "timestamp": "",
                "text": "",
                "word_count": 0,
                "has_content": False,
                "error": str(e)
            }


# 尝试导入 Windows 特定模块
try:
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
