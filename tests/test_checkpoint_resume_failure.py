"""checkpoint 断点续训失败场景 单元测试

配套 `scripts/finetune_reranker.py` 的 `load_lora_checkpoint` /
`save_lora_checkpoint` 失败路径。Daily Regression 以
`python -m unittest tests.test_checkpoint_resume_failure -v` 运行。

覆盖:
- training_state.json 损坏 → adapter 仍加载,state=None(不阻断续训)
- PEFT load_adapter 抛异常 → fallback adapter_model.pt 手动加载
- adapter 文件全部缺失 → (False, None),调用方从头开始
- 保存阶段 peft save_pretrained 抛异常 → 不抛出,仅打印
- 保存阶段 training_state 写入失败 → 不抛出
"""
import json
import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# 与 test_lora_checkpoint 相同的假 torch/peft(遵守 HF_HUB_OFFLINE 语义)
_fake_torch = types.ModuleType("torch")
_fake_torch.save = lambda obj, path: Path(path).write_bytes(pickle.dumps(obj))
_fake_torch.load = lambda path, map_location=None: pickle.loads(Path(path).read_bytes())

_fake_peft = types.ModuleType("peft")


class _FakePeftModel:
    def save_pretrained(self, *args, **kwargs):  # pragma: no cover
        pass

    def load_adapter(self, *args, **kwargs):  # pragma: no cover
        pass


_fake_peft.PeftModel = _FakePeftModel
_fake_peft.get_peft_model_state_dict = lambda m: {"lora": 1.0}
_fake_peft.set_peft_model_state_dict = lambda m, s: None


def _patch_heavy_deps():
    return mock.patch.dict(
        sys.modules,
        {"torch": _fake_torch, "peft": _fake_peft},
    )


class TestResumeTrainingStateCorrupted(unittest.TestCase):
    def test_corrupted_training_state_returns_none_state(self):
        """training_state.json 损坏 → adapter 加载成功,state=None"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.safetensors").write_bytes(b"adapter")
            (ckpt / "training_state.json").write_text("{not json", encoding="utf-8")
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)
            self.assertIsNone(state)

    def test_missing_training_state_returns_none_state(self):
        """无 training_state.json → adapter 加载成功,state=None"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.safetensors").write_bytes(b"adapter")
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)
            self.assertIsNone(state)


class TestResumeAdapterFailure(unittest.TestCase):
    def test_load_adapter_raises_fallback_to_adapter_pt(self):
        """PEFT load_adapter 抛异常 + adapter_model.pt 存在 → fallback 成功"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.pt").write_bytes(pickle.dumps({"lora": 1.0}))
            peft_mock = mock.Mock(spec=_FakePeftModel)
            peft_mock.load_adapter.side_effect = RuntimeError("boom")
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)

    def test_no_adapter_files_returns_false(self):
        """adapter 文件缺失 → (False, None),调用方从头开始"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertFalse(ok)
            self.assertIsNone(state)

    def test_both_load_paths_fail_returns_false(self):
        """load_adapter 抛异常且无 adapter_model.pt → (False, None)"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.safetensors").write_bytes(b"adapter")
            peft_mock = mock.Mock(spec=_FakePeftModel)
            peft_mock.load_adapter.side_effect = RuntimeError("boom")
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertFalse(ok)
            self.assertIsNone(state)

    def test_non_peft_state_dict_missing_returns_false(self):
        """非 PeftModel 且无 model_state.pt → (False, None)"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            ok, state = load_lora_checkpoint(_FakeModel(), ckpt)
            self.assertFalse(ok)
            self.assertIsNone(state)


class TestSaveFailureTolerant(unittest.TestCase):
    def test_save_pretrained_raises_does_not_propagate(self):
        """save_pretrained 抛异常 → save_lora_checkpoint 不抛出"""
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            peft_mock = mock.Mock(spec=_FakePeftModel)
            peft_mock.save_pretrained.side_effect = RuntimeError("disk full")
            # 不应抛异常
            save_lora_checkpoint(peft_mock, ckpt, {"epoch": 1})

    def test_training_state_write_failure_does_not_propagate(self):
        """training_state.json 写失败 → 不抛出(adapter 权重已保存)"""
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            with mock.patch("builtins.open", side_effect=OSError("denied")):
                # json.dump 走 open;adapter 保存走 Path.write_bytes(不受影响)
                save_lora_checkpoint(_FakeModel(), ckpt, {"epoch": 1})


class _FakeModel:
    """非 PeftModel 假模型"""

    def __init__(self, state=None):
        self._state = state if state is not None else {"weight": 1.0}

    def state_dict(self):
        return self._state

    def load_state_dict(self, state, strict=False):
        self._state = state
        return None


if __name__ == "__main__":
    unittest.main()
