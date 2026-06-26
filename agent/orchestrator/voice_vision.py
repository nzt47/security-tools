"""语音/视觉模块——多模态功能

从 orchestrator.py 提取，管理语音合成/识别、屏幕 OCR 等。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VoiceVision:
    """语音/视觉多模态模块

    提供语音合成（TTS）、语音识别（STT）、语音对话、
    屏幕 OCR 和多模态状态查询功能。
    """

    def __init__(self, orchestrator: Any):
        """绑定到 Orchestrator 实例

        Args:
            orchestrator: Orchestrator 实例（或其子类 DigitalLife）
        """
        self._o = orchestrator

    # ── 语音合成 ──

    def speak(self, text: str, save_to_file: bool = False):
        """语音合成

        Args:
            text: 要朗读的文本
            save_to_file: 是否保存音频到文件

        Returns:
            {"ok": bool, "text": str, "audio_path": str|None, "error": str|None}
        """
        if not self._o._voice_manager:
            return {"ok": False, "error": "语音功能未启用"}
        try:
            skill_check = self._o._is_skill_enabled("voice_interaction")
            if not skill_check:
                return {"ok": False, "error": "语音交互技能已禁用"}
        except Exception:
            pass

        try:
            logger.info("[语音] 准备说话: %s...", text[:50])
            result = self._o._voice_manager.speak(text, save_to_file)
            return {"ok": result.success, "text": text, "audio_path": result.audio_path}
        except Exception as e:
            logger.error("[FAIL] 语音合成失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ── 语音识别 ──

    def listen(self, duration: int = 5):
        """语音识别

        Args:
            duration: 录音时长（秒）

        Returns:
            {"ok": bool, "text": str, "error": str|None}
        """
        if not self._o._voice_manager:
            return {"ok": False, "error": "语音功能未启用"}

        try:
            logger.info("[语音] 开始录音 (%d秒)...", duration)
            result = self._o._voice_manager.listen(duration)
            return {"ok": result.success, "text": result.text}
        except Exception as e:
            logger.error("[FAIL] 语音识别失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ── 语音对话 ──

    def voice_chat(self, duration: int = 5, speak_response: bool = True):
        """语音对话——先听后说

        Args:
            duration: 录音时长（秒）
            speak_response: 是否朗读响应

        Returns:
            {"ok": bool, "text": str, "response": str|None, "error": str|None}
        """
        logger.info("[语音] 启动语音对话模式...")

        listen_result = self.listen(duration)
        if not listen_result.get("ok"):
            if speak_response:
                self.speak("抱歉，我没有听清您在说什么。")
            return {"ok": False, "error": listen_result.get("error"),
                    "text": None, "response": None}

        user_input = listen_result.get("text", "")
        if not user_input or not user_input.strip():
            if speak_response:
                self.speak("抱歉，我没有听到任何声音。")
            return {"ok": False, "error": "没有听到内容",
                    "text": user_input, "response": None}

        logger.info("[语音] 语音输入: %s", user_input)
        response = self._o.chat(user_input)

        if speak_response:
            self.speak(response)

        return {"ok": True, "text": user_input, "response": response}

    # ── 屏幕 OCR ──

    def look_at_screen(self, region: Optional[tuple] = None):
        """观察屏幕内容

        Args:
            region: 可选区域 (left, top, width, height)

        Returns:
            {"ok": bool, "text": str, "reading": dict, "error": str|None}
        """
        if not self._o._ocr_sensor:
            return {"ok": False, "error": "OCR功能未启用"}

        try:
            reading = self._o._ocr_sensor.capture_and_ocr(region)
            ocr_text = "\n".join(
                "[%s] %s" % (r.data.get('position', '?'), r.data.get('text', ''))
                for r in reading.data
            )
            return {
                "ok": True,
                "reading": reading.to_dict() if hasattr(reading, 'to_dict') else {},
                "text": ocr_text[:5000],
            }
        except Exception as e:
            logger.error("[FAIL] OCR失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ── 状态查询 ──

    def get_voice_status(self) -> dict:
        """获取语音功能状态"""
        if not self._o._voice_manager:
            return {"enabled": False, "available": False}

        try:
            status = self._o._voice_manager.get_status()
            return {
                "enabled": True,
                "available": True,
                "tts": status.get("tts_available", False),
                "stt": status.get("stt_available", False),
                "tts_engines": status.get("tts_engines", []),
            }
        except Exception as e:
            return {"enabled": True, "available": False, "error": str(e)}

    def get_multimodal_status(self) -> dict:
        """获取多模态功能总状态"""
        return {
            "voice": self.get_voice_status(),
            "ocr": {
                "enabled": self._o._ocr_sensor is not None,
                "available": self._o._ocr_sensor is not None,
            },
        }
