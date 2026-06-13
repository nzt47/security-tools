"""
语音处理模块 - 云枢的听觉和发声能力
支持语音识别(STT)和语音合成(TTS)
"""

import logging
import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class VoiceResult:
    """语音处理结果"""
    success: bool
    text: Optional[str] = None
    audio_path: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    timestamp: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class TTSEngine:
    """文本转语音引擎"""
    
    def __init__(self, engine: str = "gtts", output_dir: str = "./data/audio"):
        """初始化TTS引擎
        
        Args:
            engine: 引擎类型 ("pyttsx3", "gtts")
            output_dir: 音频输出目录
        """
        self.engine_type = engine
        self.output_dir = output_dir
        self.engine = None
        self.available_engines = {}
        
        os.makedirs(output_dir, exist_ok=True)
        
        self._init_engines()
    
    def _init_engines(self):
        """初始化可用的引擎"""
        # 检查gTTS（优先）
        try:
            from gtts import gTTS
            self.available_engines["gtts"] = gTTS
            logger.info("✅ gTTS 可用（在线TTS - 非阻塞）")
        except ImportError:
            logger.warning("⚠️ gTTS 未安装")
        
        # 检查pyttsx3（备用）
        try:
            import pyttsx3
            self.available_engines["pyttsx3"] = pyttsx3
            logger.info("✅ pyttsx3 可用（离线TTS - 阻塞）")
        except ImportError:
            logger.warning("⚠️ pyttsx3 未安装")
        
        # 初始化默认引擎（优先使用用户指定的引擎类型）
        if self.engine_type == "gtts" and "gtts" in self.available_engines:
            logger.info("✅ 使用 gTTS 引擎（非阻塞模式）")
        elif self.engine_type == "pyttsx3" and "pyttsx3" in self.available_engines:
            self.engine = self.available_engines["pyttsx3"].init()
            self.engine.setProperty('rate', 150)  # 语速
            self.engine.setProperty('volume', 0.9)  # 音量
            logger.info("✅ 使用 pyttsx3 引擎（阻塞模式）")
        elif "gtts" in self.available_engines:
            self.engine_type = "gtts"
            logger.info("✅ 使用 gTTS 引擎（非阻塞模式）")
        elif "pyttsx3" in self.available_engines:
            self.engine_type = "pyttsx3"
            self.engine = self.available_engines["pyttsx3"].init()
            self.engine.setProperty('rate', 150)  # 语速
            self.engine.setProperty('volume', 0.9)  # 音量
            logger.info("✅ 使用 pyttsx3 引擎（阻塞模式）")
    
    def speak(self, text: str, save_to_file: bool = False) -> VoiceResult:
        """朗读文本（非阻塞模式 - 使用gTTS生成音频文件）
        
        Args:
            text: 要朗读的文本
            save_to_file: 是否保存为文件（gTTS始终保存为文件）
            
        Returns:
            VoiceResult - 包含音频文件路径，由前端负责播放
        """
        import time
        start_time = time.time()
        logs = []
        logs.append(f"[TTS] 开始语音合成 - 文本长度: {len(text)} 字符")
        logs.append(f"[TTS] 引擎类型: {self.engine_type}")
        logs.append(f"[TTS] 模式: {'非阻塞' if self.engine_type == 'gtts' else '阻塞'}")
        
        logger.info(f"🗣️ 准备朗读: {text[:50]}...")
        
        if not self.available_engines:
            return VoiceResult(
                success=False,
                error="没有可用的TTS引擎，请安装gtts或pyttsx3"
            )
        
        try:
            # 优先使用 gTTS（非阻塞模式）
            if self.engine_type == "gtts" and "gtts" in self.available_engines:
                logs.append(f"[TTS] 使用 gTTS 引擎（非阻塞）")
                
                # 生成唯一文件名（带时间戳避免冲突）
                filename = f"tts_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.mp3"
                filepath = os.path.join(self.output_dir, filename)
                logs.append(f"[TTS] 输出文件: {filepath}")
                
                # 创建 gTTS 对象
                gtts_start = time.time()
                from gtts import gTTS
                tts = gTTS(text, lang='zh-cn', slow=False)
                gtts_time = (time.time() - gtts_start) * 1000
                logs.append(f"[TTS] gTTS对象创建完成 - 耗时: {gtts_time:.2f}ms")
                
                # 保存为文件（非阻塞，立即返回）
                save_start = time.time()
                tts.save(filepath)
                save_time = (time.time() - save_start) * 1000
                logs.append(f"[TTS] 文件保存完成 - 耗时: {save_time:.2f}ms")
                
                total_time = (time.time() - start_time) * 1000
                logs.append(f"[TTS] 完成（非阻塞）- 总耗时: {total_time:.2f}ms")
                logs.append(f"[TTS] ✅ 音频文件已生成，由前端负责播放")
                
                # 打印详细日志
                print("\n" + "="*60)
                print(f"🔊 TTS 性能日志 [gTTS - 非阻塞]")
                print("-"*60)
                for log in logs:
                    print(log)
                print("="*60 + "\n")
                
                logger.info(f"✅ 音频文件已生成: {filepath}")
                return VoiceResult(
                    success=True,
                    text=text,
                    audio_path=filepath,
                    duration=total_time / 1000
                )
            
            # 备用：pyttsx3（阻塞模式）
            elif "pyttsx3" in self.available_engines:
                logs.append(f"[TTS] 使用 pyttsx3 引擎（阻塞模式）")
                
                say_start = time.time()
                if save_to_file:
                    filename = f"tts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                    filepath = os.path.join(self.output_dir, filename)
                    logs.append(f"[TTS] 准备保存到文件: {filepath}")
                    
                    save_start = time.time()
                    self.engine.save_to_file(text, filepath)
                    save_time = (time.time() - save_start) * 1000
                    logs.append(f"[TTS] save_to_file 完成 - 耗时: {save_time:.2f}ms")
                    
                    wait_start = time.time()
                    self.engine.runAndWait()
                    wait_time = (time.time() - wait_start) * 1000
                    logs.append(f"[TTS] runAndWait 完成 - 耗时: {wait_time:.2f}ms")
                    
                    logger.info(f"✅ 保存到: {filepath}")
                else:
                    self.engine.say(text)
                    say_time = (time.time() - say_start) * 1000
                    logs.append(f"[TTS] say() 调用完成 - 耗时: {say_time:.2f}ms")
                    
                    wait_start = time.time()
                    self.engine.runAndWait()
                    wait_time = (time.time() - wait_start) * 1000
                    logs.append(f"[TTS] runAndWait 阻塞等待完成 - 耗时: {wait_time:.2f}ms")
                    logs.append(f"[TTS] ⚠️ 注意: runAndWait是阻塞调用")
                    
                    logger.info("✅ 朗读完成")
                
                total_time = (time.time() - start_time) * 1000
                logs.append(f"[TTS] 完成 - 总耗时: {total_time:.2f}ms")
                
                print("\n" + "="*60)
                print(f"🔊 TTS 性能日志 [pyttsx3 - 阻塞]")
                print("-"*60)
                for log in logs:
                    print(log)
                print("="*60 + "\n")
                
                return VoiceResult(
                    success=True,
                    text=text,
                    audio_path=filepath if save_to_file else None,
                    duration=total_time / 1000
                )
        
        except Exception as e:
            total_time = (time.time() - start_time) * 1000
            logs.append(f"[TTS] ❌ 失败 - 耗时: {total_time:.2f}ms, 错误: {str(e)}")
            logger.error(f"❌ TTS失败: {e}")
            
            return VoiceResult(
                success=False,
                text=text,
                error=str(e),
                duration=total_time / 1000
            )
    
    def set_rate(self, rate: int):
        """设置语速"""
        if self.engine and hasattr(self.engine, 'setProperty'):
            self.engine.setProperty('rate', rate)
            logger.info(f"语速设置为: {rate}")
    
    def set_volume(self, volume: float):
        """设置音量"""
        if self.engine and hasattr(self.engine, 'setProperty'):
            self.engine.setProperty('volume', volume)
            logger.info(f"音量设置为: {volume}")


class STTEngine:
    """语音识别引擎"""
    
    def __init__(self, engine: str = "speech_recognition"):
        """初始化STT引擎
        
        Args:
            engine: 引擎类型
        """
        self.engine_type = engine
        self.recognizer = None
        self.microphone = None
        self.available = False
        
        self._init_engine()
    
    def _init_engine(self):
        """初始化引擎"""
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            self.microphone = sr.Microphone()
            self.available = True
            logger.info("✅ SpeechRecognition 可用")
        except ImportError:
            logger.warning("⚠️ SpeechRecognition 未安装")
        except OSError as e:
            logger.warning(f"⚠️ 麦克风初始化失败: {e}")
            self.available = False
    
    def listen(self, duration: int = 5, language: str = "zh-CN") -> VoiceResult:
        """录音并识别
        
        Args:
            duration: 录音时长(秒)
            language: 语言
            
        Returns:
            VoiceResult
        """
        if not self.available:
            return VoiceResult(
                success=False,
                error="STT引擎不可用，请检查麦克风和SpeechRecognition库"
            )
        
        logger.info(f"🎤 开始录音 ({duration}秒)...")
        
        try:
            import speech_recognition as sr
            
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=duration)
            
            logger.info("🔄 正在识别...")
            text = self.recognizer.recognize_google(audio, language=language)
            
            logger.info(f"✅ 识别成功: {text}")
            return VoiceResult(
                success=True,
                text=text
            )
        
        except sr.UnknownValueError:
            return VoiceResult(
                success=False,
                error="无法识别语音"
            )
        except sr.RequestError as e:
            return VoiceResult(
                success=False,
                error=f"Google API请求失败: {e}"
            )
        except Exception as e:
            logger.error(f"❌ STT失败: {e}")
            return VoiceResult(
                success=False,
                error=str(e)
            )
    
    def recognize_file(self, audio_path: str, language: str = "zh-CN") -> VoiceResult:
        """从音频文件识别
        
        Args:
            audio_path: 音频文件路径
            language: 语言
            
        Returns:
            VoiceResult
        """
        if not self.available:
            return VoiceResult(
                success=False,
                error="STT引擎不可用"
            )
        
        logger.info(f"📂 识别文件: {audio_path}")
        
        try:
            import speech_recognition as sr
            
            with sr.AudioFile(audio_path) as source:
                audio = self.recognizer.record(source)
            
            text = self.recognizer.recognize_google(audio, language=language)
            
            logger.info(f"✅ 识别成功: {text}")
            return VoiceResult(
                success=True,
                text=text,
                audio_path=audio_path
            )
        
        except Exception as e:
            logger.error(f"❌ 文件识别失败: {e}")
            return VoiceResult(
                success=False,
                error=str(e)
            )


class VoiceManager:
    """语音管理 - 统一接口"""
    
    def __init__(self, tts_engine: str = "pyttsx3", audio_dir: str = "./data/audio", non_blocking: bool = True):
        """初始化语音管理器（默认使用 pyttsx3 + 多线程非阻塞模式）
        
        Args:
            tts_engine: TTS引擎类型 ("gtts" 或 "pyttsx3")
            audio_dir: 音频文件目录
            non_blocking: 是否使用非阻塞模式（多线程播放）
        """
        self.tts = TTSEngine(engine=tts_engine, output_dir=audio_dir)
        self.stt = STTEngine()
        self.non_blocking = non_blocking
        
        logger.info("🎵 语音管理器初始化完成")
        logger.info(f"   ├─ TTS: {self.tts.engine_type}")
        logger.info(f"   ├─ 模式: {'非阻塞(多线程)' if non_blocking else '阻塞'}")
        logger.info(f"   └─ STT: {'可用' if self.stt.available else '不可用'}")
    
    def speak(self, text: str, save: bool = False) -> VoiceResult:
        """朗读（支持非阻塞模式）"""
        if self.non_blocking and self.tts.engine_type == "pyttsx3":
            import threading
            thread = threading.Thread(
                target=self._speak_async,
                args=(text, save),
                daemon=True
            )
            thread.start()
            return VoiceResult(
                success=True,
                text=text,
                audio_path=None,
                duration=0.0
            )
        else:
            return self.tts.speak(text, save_to_file=save)
    
    def _speak_async(self, text: str, save: bool = False):
        """异步朗读（在后台线程中执行）"""
        try:
            self.tts.speak(text, save_to_file=save)
        except Exception as e:
            logger.error(f"异步语音播放失败: {e}")
    
    def listen(self, duration: int = 5) -> VoiceResult:
        """录音识别"""
        return self.stt.listen(duration=duration)
    
    def voice_to_text(self, audio_path: str) -> VoiceResult:
        """音频转文字"""
        return self.stt.recognize_file(audio_path)
    
    def text_to_voice(self, text: str, output_path: Optional[str] = None) -> VoiceResult:
        """文字转音频"""
        result = self.tts.speak(text, save_to_file=True)
        if output_path and result.audio_path:
            import shutil
            shutil.copy(result.audio_path, output_path)
            result.audio_path = output_path
        return result
    
    def get_status(self) -> Dict:
        """获取状态"""
        return {
            "tts_available": len(self.tts.available_engines) > 0,
            "stt_available": self.stt.available,
            "tts_engines": list(self.tts.available_engines.keys())
        }
