-- =============================================================
-- 云枢记忆系统迁移脚本：新增 user_profile 用户记忆档案卡表
-- -------------------------------------------------------------
-- 审计建议落实（四层记忆架构缺层之一：用户记忆档案卡）
--   长期事实（姓名/职业/核心目标等）使用结构化存储，不走向量库。
--   user_id 为主键 + ON CONFLICT upsert，保证事实唯一性、可覆盖更新，
--   规避纯向量库方案的"幽灵数据/新旧记录同时召回"致命问题。
--
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行。
-- 目标库：LongTermMemory 的 db_path（默认 ./data/memory/long_term.db）
--
-- 执行方式（任选其一）：
--   sqlite3 data/memory/long_term.db < scripts/dev/migrate_user_profile.sql
--   python -c "import sqlite3; sqlite3.connect('data/memory/long_term.db').executescript(open('scripts/dev/migrate_user_profile.sql', encoding='utf-8').read())"
--
-- 注：LongTermMemory._init_db() 已内嵌同构 DDL，运行时自动迁移，
--     本脚本仅用于存量库手工迁移与 CI 校验，二者保持幂等一致。
-- =============================================================

CREATE TABLE IF NOT EXISTS user_profile (
    user_id            TEXT PRIMARY KEY,   -- 用户唯一标识（事实唯一性锚点）
    name               TEXT,               -- 用户姓名
    occupation         TEXT,               -- 职业
    core_goals         TEXT,               -- 核心目标（JSON array 字符串）
    preferences        TEXT,               -- 偏好设置（JSON object 字符串）
    communication_style TEXT,              -- 沟通风格偏好（影响说话风格）
    timezone           TEXT,               -- 时区（如 Asia/Shanghai，会话元数据关联）
    device_type        TEXT,               -- 设备类型（如 mobile/desktop）
    locale             TEXT,               -- 语言环境（如 zh-CN）
    created_at         REAL NOT NULL,      -- 首次建档时间戳
    updated_at         REAL NOT NULL       -- 最近更新时间戳（upsert 覆盖时刷新）
);

-- 索引：updated_at 用于"最近更新档案"列表与过期清理
CREATE INDEX IF NOT EXISTS idx_user_profile_updated_at
    ON user_profile(updated_at);
