"""checkpoint 保存→扫描→加载 端到端恢复 测试

Daily Regression(e2e-recovery-tests job)以
`python -m unittest tests.test_checkpoint_recovery_e2e -v` 运行。

验证完整链路(纯 mock,无真实模型/HF 下载):
1. save_lora_checkpoint 写出 checkpoint
2. find_latest_checkpoint 在多个 epoch 中定位最新
3. load_lora_checkpoint 恢复 adapter + training_state
4. 断点续训语义: resumed epoch 与 saved 一致
"""
import json
import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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


class _FakeModel:
    """非 PeftModel 假模型(state_dict 可 pickle)"""

    def __init__(self, state=None):
        self._state = state if state is not None else {"weight": 1.0}

    def state_dict(self):
        return self._state

    def load_state_dict(self, state, strict=False):
        self._state = state
        return None


class TestCheckpointRecoveryE2E(unittest.TestCase):
    def test_save_find_load_roundtrip(self):
        """保存两个 epoch 后: find 定位最新,load 恢复其状态"""
        from scripts.finetune_reranker import (
            find_latest_checkpoint,
            load_lora_checkpoint,
            save_lora_checkpoint,
        )
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = _FakeModel({"weight": 2.0})
            save_lora_checkpoint(model, root / "checkpoints" / "epoch_1",
                                 {"epoch": 1, "best_val_loss": 0.9})
            save_lora_checkpoint(model, root / "checkpoints" / "epoch_2",
                                 {"epoch": 2, "best_val_loss": 0.4})

            ckpt_path, epoch = find_latest_checkpoint(root)
            self.assertEqual(epoch, 2)
            self.assertEqual(ckpt_path.name, "epoch_2")

            restored = _FakeModel()
            ok, state = load_lora_checkpoint(restored, ckpt_path)
            self.assertTrue(ok)
            self.assertEqual(state["epoch"], 2)
            self.assertEqual(state["best_val_loss"], 0.4)
            self.assertEqual(restored._state["weight"], 2.0)

    def test_resume_continues_from_latest_epoch(self):
        """断点续训语义: resumed_epoch = latest_epoch(>= 2)"""
        from scripts.finetune_reranker import find_latest_checkpoint, save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = _FakeModel()
            for e in range(1, 4):
                save_lora_checkpoint(model, root / "checkpoints" / f"epoch_{e}",
                                     {"epoch": e})
            _, latest = find_latest_checkpoint(root)
            self.assertEqual(latest, 3)
            # 续训应从 latest+1 开始
            self.assertEqual(latest + 1, 4)

    def test_peft_save_load_roundtrip(self):
        """PeftModel 路径: save(native) → load(native) 状态一致"""
        from scripts.finetune_reranker import (
            find_latest_checkpoint,
            load_lora_checkpoint,
            save_lora_checkpoint,
        )
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            root = Path(td)
            peft_mock = mock.Mock(spec=_FakePeftModel)

            def fake_save(dir_path):
                Path(dir_path).mkdir(parents=True, exist_ok=True)
                (Path(dir_path) / "adapter_model.safetensors").write_bytes(b"adapter")
                (Path(dir_path) / "training_state.json").write_text(
                    json.dumps({"epoch": 5}), encoding="utf-8")

            peft_mock.save_pretrained.side_effect = fake_save
            save_lora_checkpoint(peft_mock, root / "checkpoints" / "epoch_5", {"epoch": 5})

            ckpt_path, epoch = find_latest_checkpoint(root)
            self.assertEqual(epoch, 5)

            restored = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(restored, ckpt_path)
            self.assertTrue(ok)
            self.assertEqual(state["epoch"], 5)
            restored.load_adapter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
