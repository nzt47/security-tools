#!/usr/bin/env python3
"""离线包构建入口 —— 把导出 bundle + wheelhouse 打成一个可带走的包（S5 / portability）

【它解决什么（不这样会怎样）】
    裸 JSON 导出只声明依赖清单（environment.items），换机断网时**装不上**：
    `offline_ready` 只能恒 false。本脚本在**联网机**上把声明依赖的 wheel 下到一个
    目录（wheelhouse），把 artifacts 段写回 bundle 并重算 offline_ready，最后打成
    `<dest>.tar.gz`（内含 bundle.json + wheelhouse/ + wheelhouse_manifest.json）。
    拿到这个包的目标机才真正"断网也能装齐"。

【诚实边界】
    · 只有 operator 显式运行本脚本才会联网（`pip download`）；模块导入期零网络。
    · `--only-binary=:all:` 是默认：拿不到 wheel 的依赖会让 pip 失败并**如实报错**，
      不静默退回 sdist 源码构建（那与"离线可装"不是一回事）。要放宽用 `--allow-sdist`。
    · `--skip-download` 只扫描已有 wheelhouse（离线复算 / CI 用），不碰网络。
    · offline_ready 必须"覆盖全部声明依赖"才为真（见 dependencies.offline_ready）；
      只带几个 wheel 的包会如实保持 false。

用法：
    python scripts/build_offline_pack.py --bundle bundle.json --dest out/pack
    python scripts/build_offline_pack.py --bundle bundle.json --dest out/pack --only numpy,packaging
    python scripts/build_offline_pack.py --bundle bundle.json --dest out/pack --skip-download

退出码：0 成功；2 参数/读入错误；3 pip 下载失败；4 生成的包不合法。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile


if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:  # pragma: no cover
    # 直接 `python scripts/xxx.py` 跑时 sys.path[0] 是 scripts/，仓库根不在路径上 ⇒ 补上
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建可带走离线包（bundle + wheelhouse）")
    parser.add_argument("--bundle", required=True, help="导出的 bundle.json 路径")
    parser.add_argument("--dest", required=True, help="输出目录（会创建）")
    parser.add_argument("--only", default="", help="只给这些分发名打包（逗号分隔）")
    parser.add_argument("--allow-sdist", action="store_true",
                        help="允许 pip 取源码包（默认只取 wheel）")
    parser.add_argument("--skip-download", action="store_true",
                        help="跳过 pip download，只扫描已有 wheelhouse")
    parser.add_argument("--no-tar", action="store_true", help="不生成 tar.gz")
    return parser


def _download(specs, wheelhouse, *, allow_sdist, runner=None):
    """跑 pip download（唯一联网点；runner 可注入便于测试）"""
    os.makedirs(wheelhouse, exist_ok=True)
    argv = [sys.executable, "-m", "pip", "download", "--dest", wheelhouse]
    if not allow_sdist:
        argv += ["--only-binary", ":all:"]
    argv += list(specs)
    run = runner or subprocess.run
    try:
        proc = run(argv, capture_output=True, text=True)
    except OSError as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "argv": argv}
    return {"returncode": int(getattr(proc, "returncode", 1) or 0),
            "stdout": getattr(proc, "stdout", "") or "",
            "stderr": getattr(proc, "stderr", "") or "", "argv": argv}


def _make_tar(dest_root, out_path):
    """把输出目录打成 tar.gz（arcname = 目录名，解包后自成一个包目录）"""
    arc = os.path.basename(os.path.normpath(dest_root))
    with tarfile.open(out_path, "w:gz") as tar:
        tar.add(dest_root, arcname=arc)
    return out_path


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from agent.subagent.bundle import (bundle_from_json, bundle_to_json, validate_bundle)
    from agent.subagent.credentials import assert_manifest_secret_free
    from agent.subagent.dependencies import (apply_artifacts_to_bundle,
                                            scan_wheelhouse,
                                            wheel_specs_from_environment)

    try:
        with open(args.bundle, "r", encoding="utf-8") as fh:
            bundle = bundle_from_json(fh.read())
    except (OSError, ValueError) as exc:
        print("bundle 不可读/不合法: %s" % exc, file=sys.stderr)
        return 2

    environment = bundle.get("environment")
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    specs = wheel_specs_from_environment(environment, only=only)
    if not specs:
        print("environment 段没有可打包的声明依赖（items 为空？）", file=sys.stderr)
        return 2

    wheelhouse = os.path.join(args.dest, "wheelhouse")
    if not args.skip_download:
        result = _download(specs, wheelhouse, allow_sdist=args.allow_sdist)
        if result["returncode"] != 0:
            print("pip download 失败（rc=%s）：\n%s" % (result["returncode"],
                                                          result["stderr"][-2000:]),
                  file=sys.stderr)
            return 3
        print("[offline-pack] wheel 已下载到 %s（%d 个声明依赖）"
              % (wheelhouse, len(specs)))
    else:
        print("[offline-pack] --skip-download：只扫描 %s" % wheelhouse)

    artifacts = scan_wheelhouse(wheelhouse, write_manifest=True)
    packed = apply_artifacts_to_bundle(bundle, artifacts)
    problems = validate_bundle(packed)
    if problems:
        print("生成包不合法：\n" + "\n".join(problems[:12]), file=sys.stderr)
        return 4
    assert_manifest_secret_free(packed)

    os.makedirs(args.dest, exist_ok=True)
    bundle_out = os.path.join(args.dest, "bundle.json")
    with open(bundle_out, "w", encoding="utf-8") as fh:
        fh.write(bundle_to_json(packed))

    env_out = packed.get("environment") or {}
    declared = len(env_out.get("items") or [])
    print("[offline-pack] wheels=%d bytes=%s offline_ready=%s（声明依赖 %d）"
          % (artifacts.get("count"), artifacts.get("total_bytes"),
             env_out.get("offline_ready"), declared))
    if not env_out.get("offline_ready"):
        print("[offline-pack] 未达离线就绪：wheelhouse 未覆盖全部声明依赖，"
              "或本机 missing/version_mismatch，或 python 版本不匹配", file=sys.stderr)
    if not args.no_tar:
        tar_out = _make_tar(args.dest, os.path.normpath(args.dest) + ".tar.gz")
        print("[offline-pack] 已打包: %s" % tar_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
