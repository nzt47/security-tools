# config.yaml 的「接线状态」守卫：**别让死键假装活着**。
#
# 2026-10-03 复核结论（可复跑，证据见 docs/closeout/交付报告_20261003.md §12）：
#   · 中心配置 config.py（Config/ConfigModel）**从不解析 YAML** —— 取值链只有
#     DEFAULT → 环境变量 → overrides；memory 段的 token_limit / per_message_* 无任何读取点；
#   · 但 config.yaml **不是死文件**：autonomy / retention / learning / skills_mgmt /
#     orchestrator / planning / workflow_learning 等子系统**各自直读**它
#     （登记表中 121 个键声明了 config_path，探针实测当前 64 个由 config 层供值）。
# 所以本文件只钉两件事：
#   ① memory 段必须**显式写明未接线**（否则下一个人又会以为改了有用）；
#   ② 一旦有人真把 memory 段接进运行时，本测试必须红 —— 逼着同步改文案与常量口径。

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = REPO_ROOT / 'config.yaml'


class TestMemorySectionIsDocumentedAsUnwired:
    def test_memory_section_carries_unwired_banner(self):
        text = CONFIG_YAML.read_text(encoding='utf-8')
        head = text.split('behavior:')[0]          # memory 段在文件最前
        assert '未接线' in head or '没有任何运行时读取点' in head, (
            'config.yaml 的 memory 段必须写明「未接线」，否则读者会以为改它有效'
        )
        assert 'DEFAULT → 环境变量 → overrides' in head or '环境变量' in head

    def test_no_stale_claim_that_lifecycle_manager_reads_this_key(self):
        text = CONFIG_YAML.read_text(encoding='utf-8')
        memory_head = text.split('behavior:')[0]
        for i, line in enumerate(memory_head.splitlines(), start=1):
            if 'lifecycle_manager' not in line:
                continue
            assert any(tag in line for tag in ('更正', '不读', '并非', '不符')), (
                'config.yaml:%d（memory 段）仍有无更正的旧说法: %s' % (i, line.strip())
            )


class TestWiringStatusIsStillTrue:
    def test_central_config_does_not_read_yaml(self, monkeypatch):
        import agent.settings.resolver as resolver
        from config import Config

        monkeypatch.setattr(resolver, 'read_config_yaml',
                            lambda: {'memory': {'token_limit': 65536}}, raising=True)
        assert Config().get('memory')['token_limit'] != 65536, (
            '中心配置开始读 config.yaml 了 ⇒ 请同步更新 config.yaml 顶部说明、'
            'plugins/memory.py 的 RESTART_FALLBACK_NOTE 与本测试'
        )

    def test_config_yaml_still_has_direct_readers(self):
        """反向守卫：不能因为 memory 段没接线就把整份文件当死文件删掉。"""
        import config as cfgmod

        src = (REPO_ROOT / 'agent' / 'settings' / 'resolver.py').read_text(encoding='utf-8')
        assert 'yaml.safe_load' in src, '开关中心必须仍然真读 config.yaml'
        assert hasattr(cfgmod, 'MEMORY_TOKEN_LIMIT_DEFAULT')


class TestPluginsShareOneLimitReader:
    def test_single_implementation(self):
        from plugins import chat, memory, plugin_api

        assert chat._context_limit_info is plugin_api.context_limit_info
        assert memory._context_limit_info is plugin_api.context_limit_info

    def test_third_copy_is_gone(self):
        src = (REPO_ROOT / 'plugins' / 'status.py').read_text(encoding='utf-8')
        code = '\n'.join(l for l in src.splitlines() if not l.strip().startswith('#'))
        assert 'default=4096' not in code, 'status.py 不得再出现 4096 假分母（注释里解释可以）'
        assert 'context_limit_info(_Yunshu)' in code, '必须走单一口径'
