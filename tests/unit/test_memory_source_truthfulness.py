# 「面板文案/注释不得谎报配置来源」的守卫（2026-10-03 复核后新增）。
#
# 事实（已实测）：运行时配置合成是 DEFAULT → 环境变量 → overrides，**从不读 config.yaml**。
#   · config.py 里没有 import yaml / yaml.safe_load（源码级检查）；
#   · 把 agent.settings.resolver.read_config_yaml 谎报成 {'memory': {'token_limit': 65536}}，
#     Config().get('memory')['token_limit'] 仍返回 131072（见本文件 TestConfigYamlIsNotRuntimeSource）。
# 但插件里曾长期写着「重启后回落到 config.yaml:memory.token_limit」这类**用户可见**的说法，
# 会让人去改一个无效文件 —— 这正是本文件要钉住的漂移。

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestRestartFallbackNote:
    def test_note_names_the_real_fallback(self):
        from plugins.memory import RESTART_FALLBACK_NOTE as note

        assert '代码默认值' in note, '文案必须点名真实的回落目标'
        assert '不参与运行时配置合成' in note

    def test_note_does_not_point_users_at_config_yaml(self):
        from plugins.memory import RESTART_FALLBACK_NOTE as note

        # 允许出现 config.yaml（用于声明它无效），但不允许说“回落到 config.yaml…”
        assert not re.search(r'回落[^。；]{0,20}config\.yaml', note), note


class TestConfigYamlIsNotRuntimeSource:
    def test_lying_config_yaml_cannot_change_runtime_config(self, monkeypatch):
        import agent.settings.resolver as resolver
        from config import Config

        monkeypatch.setattr(resolver, 'read_config_yaml',
                            lambda: {'memory': {'token_limit': 65536}}, raising=True)
        assert Config().get('memory')['token_limit'] != 65536, (
            'Config 竟然读了 config.yaml —— 那么 RESTART_FALLBACK_NOTE 与多处注释都要跟着改'
        )

    def test_overrides_are_the_real_knob(self):
        from config import Config

        assert Config(overrides={'memory': {'token_limit': 4096}}).get('memory')['token_limit'] == 4096


class TestNoStaleSourceClaimInPlugins:
    _FILES = ['plugins/memory.py', 'plugins/chat.py']

    @pytest.mark.parametrize('rel', _FILES)
    def test_every_config_yaml_memory_claim_is_annotated(self, rel):
        src = (REPO_ROOT / rel).read_text(encoding='utf-8')
        for line in src.splitlines():
            if 'config.yaml:memory' not in line:
                continue
            assert any(tag in line for tag in ('更正', '无效', '不读', '从未', '不参与', '谎报')), (
                '%s 仍有无更正的旧说法: %s' % (rel, line.strip())
            )


class TestNoUserVisibleFalseFallback:
    #: 用户真正会看到的三种载体：API 文案、主 UI 组件、旧版页面
    _USER_VISIBLE = [
        'plugins/memory.py',
        'yunshu-ui/src/components/workbench/panels/ContextManagerBar.tsx',
        # 【2026-10-03 移除一项】原含 'templates/index.html' —— 该模板已随 legacy 一次性
        # 收敛退役（提交 6ab31228：/ 改重定向到 /chat#/workbench，index.html 4374 行删除），
        # 留在清单里会让本测试以 FileNotFoundError 失败（读不到文件）而非断言失败。
        # 该页面的用户可见文案已随页面消失，故移除条目是正确处置；
        # 其余两项仍在，「不得向用户谎报回落到 config.yaml」的守卫作用不变。
    ]
    #: 「回落到 config.yaml…」——运行时合成里根本不存在这一层
    _FALSE = re.compile(r'回落[^。；<"\']{0,14}config\.yaml')
    #: 引用旧文案做更正说明的行不算（必须自带这些标记）
    _EXCUSED = ('更正', '原文案', '旧文案', '写死', '谎报')

    @pytest.mark.parametrize('rel', _USER_VISIBLE)
    def test_never_promises_fallback_to_config_yaml(self, rel):
        src = (REPO_ROOT / rel).read_text(encoding='utf-8')
        for line in src.splitlines():
            if not self._FALSE.search(line):
                continue
            assert any(tag in line for tag in self._EXCUSED), (
                '%s 仍在告诉用户重启后回落 config.yaml: %s' % (rel, line.strip())
            )
