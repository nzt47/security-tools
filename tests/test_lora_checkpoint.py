"""LoRA checkpoint 保存/加载/扫描 单元测试

配套 `scripts/finetune_reranker.py` 的 `save_lora_checkpoint` /
`load_lora_checkpoint` / `find_latest_checkpoint`。

【补充背景】Daily Regression(unit-tests job)以
`python -m unittest tests.test_lora_checkpoint -v` 运行,此前该模块缺失导致
job 确定性失败(ImportError)。本文件按函数实际行为补齐契约:
- 非 PeftModel: 保存完整 state_dict(model_state.pt) + training_state.json
- PeftModel: 优先 PEFT 原生 save_pretrained,adapter 文件缺失时 fallback 手动 state_dict
- load: training_state.json 损坏不影响 adapter 加载;adapter 缺失返回 (False, None)
- find_latest_checkpoint: 按 epoch_最大N 返回最新 checkpoint

【不变式】纯 mock,不加载真实模型(torch/peft 以 sys.modules 注入假模块),
遵守 HF_HUB_OFFLINE 语义,任意环境可运行。
"""
import json
import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# 注入假 torch/peft: 避免真实导入重型依赖(遵守 CI HF_HUB_OFFLINE=1)
_fake_torch = types.ModuleType("torch")
_fake_torch.save = lambda obj, path: Path(path).write_bytes(pickle.dumps(obj))
_fake_torch.load = lambda path, map_location=None: pickle.loads(Path(path).read_bytes())

_fake_peft = types.ModuleType("peft")


class _FakePeftModel:
    """假 PeftModel(仅用于 isinstance 判定 + spec 属性约束)"""

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
    """非 PeftModel: 模拟 state_dict()/load_state_dict()"""

    def __init__(self, state=None):
        self._state = state if state is not None else {"weight": 1.0}

    def state_dict(self):
        return self._state

    def load_state_dict(self, state, strict=False):
        self._state = state
        return None


class TestFindLatestCheckpoint(unittest.TestCase):
    """纯文件系统扫描,无依赖"""

    def test_no_checkpoint_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            from scripts.finetune_reranker import find_latest_checkpoint
            self.assertIsNone(find_latest_checkpoint(Path(td)))

    def test_empty_checkpoint_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "checkpoints").mkdir()
            from scripts.finetune_reranker import find_latest_checkpoint
            self.assertIsNone(find_latest_checkpoint(Path(td)))

    def test_returns_highest_epoch(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "checkpoints"
            for e in ("epoch_1", "epoch_3", "epoch_2"):
                (ckpt / e).mkdir(parents=True)
            from scripts.finetune_reranker import find_latest_checkpoint
            path, epoch = find_latest_checkpoint(Path(td))
            self.assertEqual(epoch, 3)
            self.assertEqual(path.name, "epoch_3")

    def test_non_epoch_dirs_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "checkpoints"
            (ckpt / "epoch_2").mkdir(parents=True)
            (ckpt / "logs").mkdir()
            from scripts.finetune_reranker import find_latest_checkpoint
            path, epoch = find_latest_checkpoint(Path(td))
            self.assertEqual(epoch, 2)
            self.assertEqual(path.name, "epoch_2")


class TestSaveLoraCheckpoint(unittest.TestCase):
    def test_save_full_state_dict_non_peft(self):
        """非 PeftModel: 保存完整 state_dict + training_state"""
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            save_lora_checkpoint(_FakeModel({"a": 1.0}), ckpt,
                                 {"epoch": 1, "best_val_loss": 0.5})
            self.assertTrue((ckpt / "model_state.pt").exists())
            self.assertTrue((ckpt / "training_state.json").exists())

    def test_training_state_content(self):
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            save_lora_checkpoint(_FakeModel(), ckpt, {"epoch": 2, "patience_counter": 3})
            state = json.loads((ckpt / "training_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["epoch"], 2)
            self.assertEqual(state["patience_counter"], 3)

    def test_save_peft_uses_save_pretrained_and_cleans_full_model(self):
        """PeftModel 路径: save_pretrained 被调用;误存的完整模型被清理"""
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            peft_mock = mock.Mock(spec=_FakePeftModel)

            def fake_save_pretrained(dir_path):
                Path(dir_path).mkdir(parents=True, exist_ok=True)
                (Path(dir_path) / "adapter_model.safetensors").write_bytes(b"adapter")
                (Path(dir_path) / "model.safetensors").write_bytes(b"full")  # 误存完整模型

            peft_mock.save_pretrained.side_effect = fake_save_pretrained
            save_lora_checkpoint(peft_mock, ckpt, {"epoch": 1})
            peft_mock.save_pretrained.assert_called_once()
            self.assertTrue((ckpt / "adapter_model.safetensors").exists())
            # 误存的完整模型被清理
            self.assertFalse((ckpt / "model.safetensors").exists())

    def test_save_peft_fallback_when_no_adapter_files(self):
        """PeftModel 但 save_pretrained 未生成 adapter 文件 → fallback 手动 state_dict"""
        from scripts.finetune_reranker import save_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            peft_mock = mock.Mock(spec=_FakePeftModel)
            peft_mock.save_pretrained.side_effect = lambda dir_path: None  # 不写任何文件
            save_lora_checkpoint(peft_mock, ckpt, {"epoch": 1})
            self.assertTrue((ckpt / "adapter_model.pt").exists())


class TestLoadLoraCheckpoint(unittest.TestCase):
    def test_load_missing_adapter_returns_false(self):
        """无 adapter 文件 → (False, None)"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertFalse(ok)
            self.assertIsNone(state)

    def test_load_peft_native_returns_training_state(self):
        """adapter_model.safetensors 存在 → PEFT load_adapter → (True, state)"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.safetensors").write_bytes(b"adapter")
            (ckpt / "training_state.json").write_text(
                json.dumps({"epoch": 2}), encoding="utf-8")
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)
            self.assertEqual(state["epoch"], 2)
            peft_mock.load_adapter.assert_called_once()

    def test_load_peft_fallback_to_adapter_pt(self):
        """load_adapter 抛异常 + adapter_model.pt 存在 → fallback 手动加载"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.pt").write_bytes(pickle.dumps({"lora": 1.0}))
            peft_mock = mock.Mock(spec=_FakePeftModel)
            peft_mock.load_adapter.side_effect = RuntimeError("load_adapter boom")
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)
            self.assertIsNone(state)

    def test_load_corrupted_training_state_still_loads_adapter(self):
        """training_state.json 损坏 → adapter 仍加载,state 为 None"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "adapter_model.safetensors").write_bytes(b"adapter")
            (ckpt / "training_state.json").write_text("{invalid json", encoding="utf-8")
            peft_mock = mock.Mock(spec=_FakePeftModel)
            ok, state = load_lora_checkpoint(peft_mock, ckpt)
            self.assertTrue(ok)
            self.assertIsNone(state)

    def test_load_non_peft_state_dict(self):
        """非 PeftModel + model_state.pt → 完整 state_dict 加载"""
        from scripts.finetune_reranker import load_lora_checkpoint
        with _patch_heavy_deps(), tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "epoch_1"
            ckpt.mkdir()
            (ckpt / "model_state.pt").write_bytes(pickle.dumps({"weight": 7.0}))
            model = _FakeModel()
            ok, state = load_lora_checkpoint(model, ckpt)
            self.assertTrue(ok)
            self.assertEqual(model._state["weight"], 7.0)


if __name__ == "__main__":
    unittest.main()
