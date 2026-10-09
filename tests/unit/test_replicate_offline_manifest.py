"""replicate 页离线清单展示守卫（前端消费 bundle.environment / 导入 problems）

【为什么有这份守卫（不这样会怎样）】
    bundle.environment（#1062）与导入端 environment_check 是后端产出的**只读**数据；
    如果前端不展示，它就成了"字段在、没人读"——页面上"离线运行"那块又会退回成一句
    静态文案，与实际能力脱节（assembly.tsx 曾长期写着"可带走…尚未实现"，而 bundle
    导出/导入早已接线）。本守卫把"页面真的消费了这些字段"钉成**可证伪**断言：
    删掉展示、或把过期文案写回去，立刻红。

不 import app_server；纯源码级断言（不跑前端构建；构建产物由 build:flask 同步，
另见 tests/unit/test_legacy_surface_inventory.py 对 templates/yunshu.html 的登记）。
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPLICATE = (_REPO_ROOT / "yunshu-ui" / "src" / "pages" / "hub" / "workshop"
              / "replicate.tsx")
_ASSEMBLY = (_REPO_ROOT / "yunshu-ui" / "src" / "pages" / "hub" / "workshop"
             / "assembly.tsx")


class TestReplicateEnvironmentConsumption:
    def test_导出面消费离线依赖清单字段(self):
        src = _REPLICATE.read_text(encoding="utf-8")
        for token in ("offline_ready", "satisfied_locally", "source_status",
                      "artifacts", "counts", "python"):
            assert token in src, "replicate.tsx 未消费 bundle.environment 字段：" + token

    def test_导入面消费problems与到达端对拍(self):
        src = _REPLICATE.read_text(encoding="utf-8")
        assert "replicate-import-problems" in src, "导入 problems 没有展示位"
        assert "environment_check" in src, "导入端 environment_check 没有展示位"
        assert "ApiError" in src, "problems 在错误体里，必须经 ApiError.details 取"

    def test_assembly_过期文案已更正(self):
        src = _ASSEMBLY.read_text(encoding="utf-8")
        assert "尚未实现" not in src, "assembly 页仍在说可带走尚未实现（过期文案）"

