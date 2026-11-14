# -*- coding: utf-8 -*-
"""报警通道 ↔ .env 镜像桥（2026-11-14，alarm-channel-env-sync）

【为什么需要这一层】报警通道的值一直只存在于 agent/data/extensions.json（运行期状态，
无版本、无备份、换机即丢），而 Owner 的口径是"在 UI 配置报警通道同时也写进 env，
重新配置则修改 env，配置为空时回落到 env"。两侧必须有一处统一解释，否则
"页面显示的"与"实际发信用的"会各说各话（本仓已有两次同类教训：enabled/status 双真相、
投递台账两处写点）。

【单一真相（写死，改要同批改用例）】
1. **通道清单**（有哪些通道 / 是否启用 / 通道类型）只在 extensions.json 的 channels[]；
2. **通道值**由本模块解析出"有效配置"：本地配置 > 专用 env 键（CP_ALERT_*）> 旧键
   （SMTP_* / ERROR_REPORTING_WEBHOOK_URL）> 默认值；
3. **写入方向**：UI 保存 ⇒ 先写 .env（专用键，唯一写入口是 EnvConfigManager），
   .env 成功后写 extensions.json；两步由 ChannelInstaller.configure_channel 串起来，
   第二步失败用 .env 的 previous 回滚。

【为什么给"空"下这个定义】空 ⇒ **从 .env 删除该键**（不写 KEY= 空串占位）。
空串占位会被上游当成"已配置但为空"（ERROR_REPORTING_* 一类还会被当成 truthy），
删键才真正回到"运维手配 / 默认值"那一层 —— 这也正是 Owner 说的"配置为空时回落到 env"。

【例外（刻意）】口令字段留空 / 填 *** ⇒ **不修改**（不删键）：界面上"没填"与
"要清空"无法区分，静默删掉授权码会让通道在下一次报警时发不出去。清空必须显式
（UI 的"清除口令"按钮发 null）。

【多通道（本轮范围）】每种类型只有**主通道**（id 与 builtin 模板同名的
email_smtp / webhook）映射到 .env；同类型第 2 条起的通道只在本机登记
（否则两条通道会抢同一组键，互相覆盖）。UI 对本模块返回的 primary=False 明确标注。

【日志铁律】本模块**任何**日志/审计里都不出现通道值：
只出现通道 id、类型、以及**环境键名**。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 通道类型（extensions.json 的 config.channel_type 取值）
TYPE_SMTP = 'smtp'
TYPE_WEBHOOK = 'webhook'

#: 历史/手工安装里出现过的别名（安装器的 channel_type 与模板 id 曾经混用）
TYPE_ALIASES: Dict[str, str] = {
    'smtp': TYPE_SMTP,
    'email_smtp': TYPE_SMTP,
    'webhook': TYPE_WEBHOOK,
}

#: 每种类型的**主通道 id**（只有它镜像到 .env；见模块 docstring「多通道」）
PRIMARY_CHANNEL_BY_TYPE: Dict[str, str] = {
    TYPE_SMTP: 'email_smtp',
    TYPE_WEBHOOK: 'webhook',
}

#: 本地配置字段 → 专用环境键（写盘用；读走下面的字面量函数，见"零缺口"说明）
ENV_KEYS_BY_TYPE: Dict[str, Tuple[Tuple[str, str], ...]] = {
    TYPE_SMTP: (
        ('smtp_server', 'CP_ALERT_SMTP_HOST'),
        ('smtp_port', 'CP_ALERT_SMTP_PORT'),
        ('smtp_username', 'CP_ALERT_SMTP_USERNAME'),
        ('smtp_password', 'CP_ALERT_SMTP_PASSWORD'),
        ('from_address', 'CP_ALERT_SMTP_FROM'),
        ('default_to', 'CP_ALERT_SMTP_TO'),
    ),
    TYPE_WEBHOOK: (
        ('webhook_url', 'CP_ALERT_WEBHOOK_URL'),
    ),
}

#: 回落层（运维手配的老键；只读，不由本模块写）
LEGACY_ENV_BY_TYPE: Dict[str, Tuple[Tuple[str, str], ...]] = {
    TYPE_SMTP: (
        ('smtp_server', 'SMTP_HOST'),
        ('smtp_port', 'SMTP_PORT'),
        ('smtp_username', 'SMTP_USERNAME'),
        ('smtp_password', 'SMTP_PASSWORD'),
        ('from_address', 'MAIL_FROM'),
        ('default_to', 'MAIL_RECIPIENTS'),
    ),
    TYPE_WEBHOOK: (
        ('webhook_url', 'ERROR_REPORTING_WEBHOOK_URL'),
    ),
}

#: 口令类字段（对外永久掩码；留空 = 不改）
SECRET_FIELDS = frozenset({'smtp_password'})

#: 掩码常量（唯一形态；不保留任何片段/长度）
MASK = '***'

#: 只存本地登记的字段（传输细节，不是凭据/端点；不映射 env）
LOCAL_ONLY_FIELDS: Tuple[str, ...] = (
    'channel_type', 'method', 'headers', 'use_tls', 'use_ssl',
)

#: 有效值来源
SOURCE_CHANNEL = 'channel'
SOURCE_ENV = 'env'
SOURCE_LEGACY_ENV = 'legacy_env'
SOURCE_DEFAULT = 'default'

SOURCE_LABELS: Dict[str, str] = {
    SOURCE_CHANNEL: '通道本地配置',
    SOURCE_ENV: '.env（CP_ALERT_*）',
    SOURCE_LEGACY_ENV: '.env（旧键 SMTP_* / ERROR_REPORTING_*）',
    SOURCE_DEFAULT: '默认值（未配置）',
}

#: 空通道的**范例**（前端空态直接可复制；后端是唯一来源，不在前端再抄一份）
EXAMPLE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    TYPE_SMTP: {
        'channel_type': 'smtp',
        'smtp_server': 'smtp.example.com',
        'smtp_port': 465,
        'smtp_username': 'alert@example.com',
        'smtp_password': '（填邮箱授权码，不是登录密码）',
        'from_address': 'alert@example.com',
        'default_to': 'owner@example.com',
        'use_tls': True,
    },
    TYPE_WEBHOOK: {
        'channel_type': 'webhook',
        'webhook_url': 'https://example.com/hooks/yunshu-alert',
        'method': 'POST',
        'headers': {'Content-Type': 'application/json'},
    },
}

#: 空态说明（与范例一起展示；前端不自造文案）
EXAMPLE_NOTES: Dict[str, Tuple[str, ...]] = {
    TYPE_SMTP: (
        '端口：465 = 隐式 TLS（常见于 139/163），587 = STARTTLS。',
        '口令填邮箱的"授权码"，不是网页登录密码。',
        '收件人可用逗号分隔多人；留空则该通道发不出去（会记投递失败并报警）。',
        '保存后这些值会同时写进 .env（CP_ALERT_SMTP_*），重启后仍然生效。',
    ),
    TYPE_WEBHOOK: (
        '地址要能被本机直接访问（不要填内网占位域名）。',
        '保存后地址会写进 .env（CP_ALERT_WEBHOOK_URL），重启后仍然生效。',
        '留空并保存 = 从 .env 删除该键 ⇒ 回落到旧键 ERROR_REPORTING_WEBHOOK_URL / 默认值。',
    ),
}


def channel_type(row: Mapping[str, Any]) -> str:
    """一条通道的规范化类型（未知类型原样返回空串）"""
    cfg = row.get('config')
    raw = ''
    if isinstance(cfg, Mapping):
        raw = str(cfg.get('channel_type') or '')
    if not raw:
        raw = str(row.get('ext_type') or '')
    return TYPE_ALIASES.get(raw, '')


def is_primary_channel(row: Mapping[str, Any]) -> bool:
    """是否该类型的**主通道**（只有主通道映射 .env）"""
    ctype = channel_type(row)
    if not ctype:
        return False
    return PRIMARY_CHANNEL_BY_TYPE.get(ctype) == str(row.get('ext_id') or '')


def env_keys_for(ctype: str) -> List[str]:
    """该类型映射的专用环境键名（顺序稳定；审计/展示用）"""
    return [name for _field, name in ENV_KEYS_BY_TYPE.get(ctype, ())]


def _as_text(value: Any) -> str:
    return '' if value is None else str(value).strip()


def _dedicated_env_values(ctype: str) -> Dict[str, str]:
    """读专用环境键（**逐条字面量**）

    【为什么不写成 for 循环 / f-string】本仓的「开关零缺口」门禁用 AST 机械提取
    env 读取点；变量名形式的读取会落进 <unresolved> 动态家族而判红
    （agent/extensions/channel_bridge.py::_event_enabled 有同款注释与教训）。
    新增键时必须同步登记 agent/settings/registry.py。
    """
    if ctype == TYPE_SMTP:
        return {
            'smtp_server': os.environ.get('CP_ALERT_SMTP_HOST', ''),
            'smtp_port': os.environ.get('CP_ALERT_SMTP_PORT', ''),
            'smtp_username': os.environ.get('CP_ALERT_SMTP_USERNAME', ''),
            'smtp_password': os.environ.get('CP_ALERT_SMTP_PASSWORD', ''),
            'from_address': os.environ.get('CP_ALERT_SMTP_FROM', ''),
            'default_to': os.environ.get('CP_ALERT_SMTP_TO', ''),
        }
    if ctype == TYPE_WEBHOOK:
        return {'webhook_url': os.environ.get('CP_ALERT_WEBHOOK_URL', '')}
    return {}


def _legacy_env_values(ctype: str) -> Dict[str, str]:
    """读回落层的旧键（只读；这些键属于审计邮件 / 错误上报，本模块不写它们）"""
    if ctype == TYPE_SMTP:
        return {
            'smtp_server': os.environ.get('SMTP_HOST', ''),
            'smtp_port': os.environ.get('SMTP_PORT', ''),
            'smtp_username': os.environ.get('SMTP_USERNAME', ''),
            'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
            'from_address': os.environ.get('MAIL_FROM', ''),
            'default_to': os.environ.get('MAIL_RECIPIENTS', ''),
        }
    if ctype == TYPE_WEBHOOK:
        return {'webhook_url': os.environ.get('ERROR_REPORTING_WEBHOOK_URL', '')}
    return {}


def _local_values(row: Mapping[str, Any]) -> Dict[str, str]:
    cfg = row.get('config')
    cfg = cfg if isinstance(cfg, Mapping) else {}
    ctype = channel_type(row)
    return {field: _as_text(cfg.get(field))
            for field, _name in ENV_KEYS_BY_TYPE.get(ctype, ())}


def _resolve_layers(row: Mapping[str, Any]) -> Dict[str, Tuple[str, str]]:
    """逐字段解析生效值：{字段: (值, 来源)}

    次序：本地配置 > 专用 env > 旧 env > 默认（空串）。
    **非主通道**只有本地配置这一层（否则第二条 SMTP 会借用主通道的凭据）。
    """
    ctype = channel_type(row)
    local = _local_values(row)
    primary = is_primary_channel(row)
    dedicated = _dedicated_env_values(ctype) if primary else {}
    legacy = _legacy_env_values(ctype) if primary else {}
    out: Dict[str, Tuple[str, str]] = {}
    for field, _name in ENV_KEYS_BY_TYPE.get(ctype, ()):
        for source, layer in ((SOURCE_CHANNEL, local),
                              (SOURCE_ENV, dedicated),
                              (SOURCE_LEGACY_ENV, legacy)):
            value = _as_text(layer.get(field))
            if value:
                out[field] = (value, source)
                break
        else:
            out[field] = ('', SOURCE_DEFAULT)
    return out


def effective_config(row: Mapping[str, Any]) -> Dict[str, Any]:
    """发送侧用的**有效配置**（真实值，含回落层；不要直接给 UI）

    非映射字段（method / headers / use_tls / use_ssl 等）原样带出。
    """
    cfg = row.get('config')
    out: Dict[str, Any] = dict(cfg) if isinstance(cfg, Mapping) else {}
    if not str(out.get('channel_type') or ''):
        ctype = channel_type(row)
        if ctype:
            out['channel_type'] = ctype
    for field, (value, _source) in _resolve_layers(row).items():
        if value:
            out[field] = value
        else:
            out.pop(field, None)
    return out


def public_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    """UI 展示用视图（口令永久掩码；逐字段带来源，供"来自 .env"徽标）"""
    ctype = channel_type(row)
    primary = is_primary_channel(row)
    cfg = row.get('config')
    cfg = cfg if isinstance(cfg, Mapping) else {}
    layers = _resolve_layers(row)
    fields: List[Dict[str, Any]] = []
    for field, env_key in ENV_KEYS_BY_TYPE.get(ctype, ()):
        value, source = layers.get(field, ('', SOURCE_DEFAULT))
        local = _as_text(cfg.get(field))
        secret = field in SECRET_FIELDS
        fields.append({
            'field': field,
            'env_key': env_key,
            'secret': secret,
            'local': (MASK if (secret and local) else local),
            'local_configured': bool(local),
            'effective': (MASK if (secret and value) else value),
            'source': source,
            'source_label': SOURCE_LABELS.get(source, source),
            'configured': bool(value),
        })
    extra = {str(k): v for k, v in cfg.items()
             if str(k) not in LOCAL_ONLY_FIELDS
             and str(k) not in {f for f, _n in ENV_KEYS_BY_TYPE.get(ctype, ())}}
    return {
        'channel_id': str(row.get('ext_id') or ''),
        'channel_type': ctype,
        'primary': primary,
        'env_keys': env_keys_for(ctype),
        'fields': fields,
        'extra': extra,
        'example': EXAMPLE_TEMPLATES.get(ctype, {}),
        'notes': list(EXAMPLE_NOTES.get(ctype, ())),
        'source_labels': dict(SOURCE_LABELS),
    }


def _build_env_plan(row: Mapping[str, Any],
                    incoming: Mapping[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    """把一次保存的入参折算成 .env 计划：{键: 值} + 待删键"""
    ctype = channel_type(row)
    upsert: Dict[str, str] = {}
    delete: List[str] = []
    for field, env_key in ENV_KEYS_BY_TYPE.get(ctype, ()):
        if field not in incoming:
            continue                       # 本次没提交这个字段 ⇒ 不碰
        raw = incoming.get(field)
        text = _as_text(raw)
        if field in SECRET_FIELDS and raw is not None and text in ('', MASK):
            continue                       # 留空 / 掩码 = 不改（清空必须显式 null）
        if raw is None or not text:
            delete.append(env_key)
            continue
        upsert[env_key] = text
    return upsert, delete


def normalize_config(row: Mapping[str, Any],
                     incoming: Mapping[str, Any]) -> Dict[str, Any]:
    """入参 → 要落本地存储的 config（口令留空/掩码时保留原值）"""
    out: Dict[str, Any] = {}
    for key, value in incoming.items():
        name = str(key)
        if name in SECRET_FIELDS and value is not None and _as_text(value) in ('', MASK):
            continue                       # 留空 / 掩码 = 不改口令（null 才是清空）
        # 显式 null = 清空：本地也记成空串（与 "env 里删键" 同一步，避免留下 None）
        out[name] = "" if value is None else value
    return out


def _audit(action: str, row: Mapping[str, Any], *,
           written: Sequence[str] = (), removed: Sequence[str] = ()) -> None:
    """通道配置留痕（**只记键名**；值/长度/布尔一律不进链）"""
    payload: Dict[str, Any] = {
        'channel_id': str(row.get('ext_id') or ''),
        'channel_type': channel_type(row),
        'env_keys_written': list(written),
        'env_keys_removed': list(removed),
    }
    try:
        from agent.audit.facade import audit
        audit.record(action, actor='channel-config',
                     subject='channel:' + str(row.get('ext_id') or ''),
                     payload=payload, source='ui')
    except Exception as exc:                       # noqa: BLE001 留痕失败不阻塞配置写入
        logger.debug('[通道环境桥] 审计写入跳过（%s）', type(exc).__name__)


def sync_env(row: Mapping[str, Any],
             incoming: Mapping[str, Any]) -> Dict[str, Any]:
    """把一次保存镜像进 .env（**只对主通道生效**）

    Returns:
        {"primary": bool, "written": [...], "removed": [...], "unchanged": [...],
         "previous": {键: 旧值或 None}, "wrote_file": bool}
        previous 供调用方在后续落盘失败时回滚。
    """
    out: Dict[str, Any] = {'primary': is_primary_channel(row), 'written': [],
                           'removed': [], 'unchanged': [], 'previous': {},
                           'wrote_file': False}
    if not out['primary']:
        return out
    upsert, delete = _build_env_plan(row, incoming)
    if not upsert and not delete:
        return out
    from agent.env_config_manager import get_env_config_manager
    result = get_env_config_manager().apply_many(upsert=upsert, delete=delete)
    out.update({
        'written': list(result.get('written') or []),
        'removed': list(result.get('removed') or []),
        'unchanged': list(result.get('unchanged') or []),
        'previous': dict(result.get('previous') or {}),
        'wrote_file': bool(result.get('wrote_file')),
    })
    if out['written'] or out['removed']:
        _audit('channel.env_sync', row, written=out['written'],
               removed=out['removed'])
    return out


def rollback_env(row: Mapping[str, Any],
                 previous: Mapping[str, Optional[str]]) -> Dict[str, Any]:
    """按 previous 回滚 .env（把旧值写回、旧值不存在的键删掉）"""
    if not previous:
        return {'written': [], 'removed': []}
    restore: Dict[str, str] = {}
    drop: List[str] = []
    for key, value in previous.items():
        name = str(key)
        if value is None or not str(value).strip():
            drop.append(name)
        else:
            restore[name] = str(value)
    from agent.env_config_manager import get_env_config_manager
    result = get_env_config_manager().apply_many(upsert=restore, delete=drop)
    _audit('channel.env_rollback', row,
           written=list(result.get('written') or []),
           removed=list(result.get('removed') or []))
    return {'written': list(result.get('written') or []),
            'removed': list(result.get('removed') or [])}


def clear_env(row: Mapping[str, Any]) -> Dict[str, Any]:
    """清掉该通道在 .env 里的专用键（卸载主通道时用；非主通道是 no-op）"""
    out: Dict[str, Any] = {'primary': is_primary_channel(row),
                           'written': [], 'removed': [], 'previous': {}}
    if not out['primary']:
        return out
    keys = env_keys_for(channel_type(row))
    if not keys:
        return out
    from agent.env_config_manager import get_env_config_manager
    result = get_env_config_manager().apply_many(delete=keys)
    out['removed'] = list(result.get('removed') or [])
    if out['removed']:
        _audit('channel.env_clear', row, removed=out['removed'])
    return out


__all__ = [
    'TYPE_SMTP', 'TYPE_WEBHOOK', 'TYPE_ALIASES', 'PRIMARY_CHANNEL_BY_TYPE',
    'ENV_KEYS_BY_TYPE', 'LEGACY_ENV_BY_TYPE', 'SECRET_FIELDS', 'MASK',
    'LOCAL_ONLY_FIELDS', 'SOURCE_LABELS', 'EXAMPLE_TEMPLATES', 'EXAMPLE_NOTES',
    'channel_type', 'is_primary_channel', 'env_keys_for', 'effective_config',
    'public_view', 'normalize_config', 'sync_env', 'rollback_env', 'clear_env',
]
