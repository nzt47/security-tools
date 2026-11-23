"""`look_at_screen` 的接线守卫（2026-11-24 修）。

【修的缺陷】旧 `VoiceVision.look_at_screen` 调 `OcrSensor.capture_and_ocr` 并解包
`reading.data` / `reading.to_dict()`，但真实 `sensor/ocr_sensor.OcrSensor` 只有
`capture_and_recognize()`（返回普通 dict）⇒ 成功分支永远走不到，工具恒回
`{ok:false,error:"'OcrSensor' object has no attribute 'capture_and_ocr'"}`。
另：模块顶部只 import 了 Optional 却在注解里用 Any ⇒ `import agent.orchestrator.voice_vision`
直接 NameError（`dl.voice` 懒加载即抛）。

本文件钉住三件事：
1. 模块可导入（Any 注解不再炸）；
2. 成功路径按 `capture_and_recognize` 的真实返回组 `{ok,reading,text}`，不猜 `.data`；
3. 传感器报错 / 未启用时仍 fail-closed（ok=False + 稳定 error 文案），
   且两种返回都通过 `data/tool_definitions/look_at_screen.yaml` 的 result_schema。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from agent.capregistry.contract import validate_against_schema
from agent.orchestrator.voice_vision import VoiceVision

ROOT = Path(__file__).resolve().parents[2]


class _Sensor:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    def capture_and_recognize(self, region=None):
        if self.exc is not None:
            raise self.exc
        return self.result


class _Orchestrator:
    def __init__(self, sensor):
        self._ocr_sensor = sensor


def _view(sensor):
    return VoiceVision(_Orchestrator(sensor))


def _schema() -> dict:
    doc = yaml.safe_load((ROOT / "data" / "tool_definitions" / "look_at_screen.yaml")
                         .read_text(encoding="utf-8"))
    return doc["result_schema"]


REAL_READING = {
    "timestamp": "2026-11-24 02:10:00",
    "text": "Hello screen",
    "word_count": 2,
    "has_content": True,
    "char_count": 12,
}


class Test接线:
    def test_模块可导入且成功路径返回真实形状(self):
        out = _view(_Sensor(REAL_READING)).look_at_screen()
        assert out["ok"] is True
        assert out["text"] == "Hello screen"
        assert out["reading"] == REAL_READING, "reading 必须是 capture_and_recognize 的原样 dict"

    def test_长文本截断到5000(self):
        reading = dict(REAL_READING, text="x" * 9000, char_count=9000)
        out = _view(_Sensor(reading)).look_at_screen()
        assert out["ok"] is True and len(out["text"]) == 5000

    def test_传感器报错时_fail_closed(self):
        out = _view(_Sensor({"timestamp": "", "text": "",
                             "word_count": 0, "has_content": False,
                             "error": "OCR 库不可用"})).look_at_screen()
        assert out["ok"] is False and out["error"] == "OCR 库不可用"

    def test_未启用时_fail_closed(self):
        out = VoiceVision(_Orchestrator(None)).look_at_screen()
        assert out["ok"] is False and out["error"] == "OCR功能未启用"

    def test_传感器抛异常时_fail_closed(self):
        out = _view(_Sensor(exc=RuntimeError("boom"))).look_at_screen()
        assert out["ok"] is False and "boom" in out["error"]

    def test_成功与失败样本都满足_result_schema(self):
        schema = _schema()
        ok, errs, validator = validate_against_schema(
            _view(_Sensor(REAL_READING)).look_at_screen(), schema)
        assert ok, (validator, errs)
        ok, errs, validator = validate_against_schema(
            _view(_Sensor({"text": "", "error": "OCR 库不可用"})).look_at_screen(), schema)
        assert ok, (validator, errs)
