# mini_strategy_table.py：用一张 frozen dataclass 表描述 N 个外部 CLI 的差异
import os
import shutil
import subprocess
from dataclasses import dataclass

@dataclass(frozen=True)
class TargetConfig:
    """每个外部目标的适配配置"""
    cmd: str                    # 必填：命令名
    label: str                  # 必填：人类可读名
    env_overrides: tuple[tuple[str, str], ...] = ()   # (变量名, 模板) 对，用 tuple 保持不可变
    args_prefix: tuple[str, ...] = ()
    cleanup_env: tuple[str, ...] = ()
    needs_special_handling: bool = False

# 关键：所有差异在这一张表里
TARGETS: dict[str, TargetConfig] = {
    "claude": TargetConfig(
        cmd="claude",
        label="Claude Code",
        env_overrides=(("ANTHROPIC_BASE_URL", "http://127.0.0.1:{port}"),),
        cleanup_env=("CLAUDECODE",),
    ),
    "codex": TargetConfig(
        cmd="codex",
        label="Codex",
        env_overrides=(("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),),
        needs_special_handling=True,
    ),
}

# 通用逻辑：从配置驱动
def run(target_name: str, port: int, args: list[str]) -> int:
    cfg = TARGETS.get(target_name)
    if cfg is None:
        raise ValueError(f"Unknown target: {target_name}")
    resolved = shutil.which(cfg.cmd)
    if not resolved:
        print(f"Install {cfg.label} first")
        return 1
    env = os.environ.copy()
    for key, tmpl in cfg.env_overrides:
        env[key] = tmpl.format(port=port)
    for key in cfg.cleanup_env:
        env.pop(key, None)
    if cfg.needs_special_handling:
        args = _handle_special(cfg.cmd, args)  # escape hatch
    return subprocess.call([resolved] + list(cfg.args_prefix) + args, env=env)

def _handle_special(cmd: str, args: list[str]) -> list[str]:
    # 个别 target 的特殊 patch
    if cmd == "codex" and args and args[0] == "old-subcmd":
        return ["new-subcmd"] + args[1:]
    return args


if __name__ == "__main__":
    # 演示：不真正启动 CLI，只打印 claude 这一行配置解析出的环境变量
    cfg = TARGETS["claude"]
    print(cfg.label, {k: t.format(port=8080) for k, t in cfg.env_overrides}, "清理:", cfg.cleanup_env)
    print(_handle_special("codex", ["old-subcmd", "x"]))
