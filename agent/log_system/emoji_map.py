"""Windows GBK 编码兼容性处理 - Emoji 替换"""
import logging

_EMOJI_MAP = {
    '🚀': '[ROCKET]', '📋': '[LIST]', '🎛️': '[CONTROL]', '✅': '[OK]',
    '❌': '[FAIL]', '⚠️': '[WARN]', '🔒': '[LOCK]', '🔓': '[UNLOCK]',
    '📦': '[PACKAGE]', '🔄': '[RELOAD]', '📊': '[CHART]', '🔍': '[SEARCH]',
    '💡': '[IDEA]', '🔥': '[FIRE]', '✨': '[STAR]', '🎉': '[PARTY]',
    '👏': '[CLAP]', '👍': '[THUMBS_UP]', '💬': '[CHAT]', '⏳': '[WAIT]',
    '⌛': '[TIME]', '📈': '[UP]', '📉': '[DOWN]', '🎯': '[TARGET]',
    '💻': '[PC]', '📱': '[PHONE]', '🔧': '[TOOL]', '⚙️': '[SETTINGS]',
    '🔌': '[PLUG]', '💾': '[SAVE]', '📝': '[EDIT]', '🔗': '[LINK]',
    '💎': '[DIAMOND]', '🏆': '[TROPHY]', '🎮': '[GAME]', '👤': '[USER]',
    '👥': '[USERS]', '👨‍💻': '[DEV]', '🤖': '[ROBOT]', '💀': '[SKULL]',
    '💩': '[POO]', '👻': '[GHOST]', '🤝': '[HAND_SHAKE]', '👋': '[WAVE]',
    '💪': '[MUSCLE]', '👀': '[EYES]', '💭': '[THINK]', '😀': '[SMILE]',
    '😂': '[TEARS]', '😊': '[BLUSH]', '😍': '[HEART_EYES]', '🤔': '[THINKING]',
    '🙄': '[EYE_ROLL]', '😴': '[SLEEPING]', '😎': '[COOL]', '🤓': '[NERD]',
    '😕': '[CONFUSED]', '😟': '[WORRIED]', '😭': '[LOUDLY]', '😡': '[ANGRY]',
    '🤬': '[SHOUTING]', '🎃': '[PUMPKIN]', '🎅': '[SANTA]', '🎆': '[FIREWORKS]',
    '📌': '[PIN]', '📍': '[MAP_PIN]', '📧': '[EMAIL]', '📨': '[INBOX]',
    '📤': '[OUTBOX]', '📥': '[INCOMING]', '📫': '[MAILBOX]', '✉️': '[LETTER]',
    '🔖': '[LABEL]', '🏷️': '[TAG]', '💳': '[CREDIT_CARD]', '💰': '[MONEY_BAG]',
    '💰': '[MONEY_BAG]', '💸': '[MONEY_FLY]', '📁': '[FOLDER]',
    '📂': '[OPEN_FOLDER]', '📅': '[CALENDAR]', '📚': '[BOOKS]',
    '🔐': '[UNLOCKED]', '🔑': '[KEY]', '🛑': '[STOP]',
    '🚫': '[NO]', '📶': '[SIGNAL]', '💾': '[SAVE]',
}

def _safe_log_message(message):
    """替换 emoji 避免 GBK 编码问题"""
    if not isinstance(message, str):
        return message
    for emoji, replacement in _EMOJI_MAP.items():
        message = message.replace(emoji, replacement)
    return message

class EmojiFilter(logging.Filter):
    """日志过滤器 - 自动替换 emoji 字符"""
    def filter(self, record):
        if record.msg is not None:
            record.msg = _safe_log_message(record.msg)
        if record.args:
            record.args = tuple(
                _safe_log_message(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True
