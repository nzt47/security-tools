/**
 * observability.ts 单元测试
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  getObservabilityConfig,
  isObservabilityEnabled,
  shouldSample,
  __resetObservabilityConfigForTest,
  ObservabilityErrorCode,
} from './observability';

describe('observability 配置层', () => {
  beforeEach(() => {
    __resetObservabilityConfigForTest();
    vi.unstubAllEnvs();
  });

  it('默认 dev 环境下启用可观测性', () => {
    // vitest 默认 DEV=true，且未 stub VITE_OBSERVABILITY_ENABLED
    expect(isObservabilityEnabled()).toBe(true);
    const cfg = getObservabilityConfig();
    expect(cfg.devConsoleEnabled).toBe(true);
    expect(cfg.maxRecords).toBe(200);
    expect(cfg.samplingRate).toBe(1);
  });

  it('VITE_OBSERVABILITY_ENABLED=false 时禁用', () => {
    vi.stubEnv('VITE_OBSERVABILITY_ENABLED', 'false');
    __resetObservabilityConfigForTest();
    expect(isObservabilityEnabled()).toBe(false);
    const cfg = getObservabilityConfig();
    expect(cfg.devConsoleEnabled).toBe(false);
  });

  it('VITE_OBSERVABILITY_ENABLED=true 优先于 DEV', () => {
    vi.stubEnv('VITE_OBSERVABILITY_ENABLED', 'true');
    __resetObservabilityConfigForTest();
    expect(isObservabilityEnabled()).toBe(true);
  });

  it('自定义 maxRecords 生效', () => {
    vi.stubEnv('VITE_OBS_MAX_RECORDS', '500');
    __resetObservabilityConfigForTest();
    expect(getObservabilityConfig().maxRecords).toBe(500);
  });

  it('maxRecords 超范围时回退到禁用配置（不影响业务）', () => {
    vi.stubEnv('VITE_OBS_MAX_RECORDS', '9999');
    __resetObservabilityConfigForTest();
    const cfg = getObservabilityConfig();
    // 回退到安全默认值
    expect(cfg.devConsoleEnabled).toBe(false);
    expect(cfg.maxRecords).toBe(200);
  });

  it('自定义 samplingRate 生效', () => {
    vi.stubEnv('VITE_OBS_SAMPLING_RATE', '0.5');
    __resetObservabilityConfigForTest();
    expect(getObservabilityConfig().samplingRate).toBe(0.5);
  });

  it('samplingRate 超范围时回退', () => {
    vi.stubEnv('VITE_OBS_SAMPLING_RATE', '2');
    __resetObservabilityConfigForTest();
    const cfg = getObservabilityConfig();
    expect(cfg.devConsoleEnabled).toBe(false);
  });

  it('shouldSample 在 samplingRate=1 时全量采集', () => {
    __resetObservabilityConfigForTest();
    expect(shouldSample()).toBe(true);
  });

  it('shouldSample 在 samplingRate=0 时不采集', () => {
    vi.stubEnv('VITE_OBS_SAMPLING_RATE', '0');
    __resetObservabilityConfigForTest();
    expect(shouldSample()).toBe(false);
  });

  it('配置缓存：第二次调用返回同一实例', () => {
    const a = getObservabilityConfig();
    const b = getObservabilityConfig();
    expect(a).toBe(b);
  });

  it('错误码枚举值正确', () => {
    expect(ObservabilityErrorCode.INVALID_MAX_RECORDS).toBe('OBS_ERR_001');
    expect(ObservabilityErrorCode.INVALID_SAMPLING_RATE).toBe('OBS_ERR_002');
  });
});
