#!/usr/bin/env python3
"""Create and check the agent-trader data dir. Pure stdlib; prints JSON for agents.

usage:
  python3 setup.py init --account <ACCOUNT_NUMBER> [--dir DIR] [--force]
  python3 setup.py doctor [--dir DIR]

init    creates DIR (default: the project root, see datadir.py) with config.json from
        config.example.json, plus state/ run/ journal/ reports/. Never overwrites an existing
        config.json unless --force. The account number must come from the user.
doctor  checks Python, the data dir, config.json (account set, no missing keys), the kill/halt
        flags and that the data dir is not tracked by git. Exit 0 if no problems.
The Robinhood MCP connection cannot be checked from here; the agent checks it (SKILL.md).
"""
import json
import subprocess
import sys
from pathlib import Path

import datadir

SKILL = Path(__file__).resolve().parent.parent
EXAMPLE = SKILL / "config.example.json"
PLACEHOLDER = "YOUR_ACCOUNT_NUMBER"
OPTIONAL_KEYS = {"resize"}  # read with .get(); older configs work without them
JOURNAL_HEADERS = {"equity.csv": "date,equity,spy\n", "flows.csv": "date,amount,note\n", "trades.jsonl": ""}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else default


def missing_keys(example, cfg, prefix=""):
    out = []
    for k, v in example.items():
        if k not in cfg:
            out.append(prefix + k)
        elif isinstance(v, dict) and isinstance(cfg[k], dict):
            out += missing_keys(v, cfg[k], f"{prefix}{k}.")
    return out


def git_tracked_risk(root):
    """Return a warning if root sits inside a git work tree and is not git-ignored."""
    try:
        inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True).stdout.strip() == "true"
        if not inside:
            return None
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "config.json"]).returncode == 0
    except OSError:
        return None
    return None if ignored else f"{root} is inside a git repo and config.json is not git-ignored: add the data dir to .gitignore"


def next_steps(root):
    steps = []
    if datadir.resolve() != root:
        steps.append(f'set AGENT_TRADER_HOME="{root}" in the environment your agent runs scripts in, '
                     "so the scripts find this data dir")
    steps.append("connect the robinhood-trading MCP server (https://agent.robinhood.com/mcp/trading) and authenticate it "
                 "in your agent; see https://robinhood.com/us/en/support/articles/agentic-trading-overview/#ConnectyourAIagent")
    steps.append("run `/trade check` during market hours; the first live run shows the order table and waits for approval")
    return steps


def init():
    account = (arg("--account") or "").strip()
    if not account or account == PLACEHOLDER:
        return {"ok": False, "problems": ["--account <ACCOUNT_NUMBER> is required; ask the user (never pick one from get_accounts)"]}
    root = datadir.resolve(arg("--dir"))
    cfg_path, created = root / "config.json", []
    root.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists() and "--force" not in sys.argv:
        current = json.loads(cfg_path.read_text()).get("account_number")
        if current != account:
            return {"ok": False, "data_dir": str(root),
                    "problems": [f"config.json already exists for account {current}; pass --force to replace it"]}
    else:
        cfg = json.loads(EXAMPLE.read_text())
        cfg["account_number"] = account
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
        created.append("config.json")
    for d in ("state", "run", "journal", "reports"):
        if not (root / d).exists():
            (root / d).mkdir()
            created.append(d + "/")
    for name, header in JOURNAL_HEADERS.items():
        p = root / "journal" / name
        if not p.exists():
            p.write_text(header)
            created.append(f"journal/{name}")
    report = doctor(root)
    report.update(created=created, next_steps=next_steps(root))
    return report


def doctor(root=None):
    root = root or datadir.resolve(arg("--dir"))
    problems, warnings = [], []
    if sys.version_info < (3, 9):
        problems.append(f"Python {sys.version.split()[0]} is too old: need 3.9+ (zoneinfo)")
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        problems.append(f"no config.json in {root}: run `setup.py init --account <ACCOUNT_NUMBER>`")
        return {"ok": False, "data_dir": str(root), "problems": problems, "warnings": warnings}
    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        problems.append(f"config.json is not valid JSON: {e}")
        return {"ok": False, "data_dir": str(root), "problems": problems, "warnings": warnings}
    if cfg.get("account_number") in (None, "", PLACEHOLDER):
        problems.append("config.json account_number is not set")
    for k in missing_keys(json.loads(EXAMPLE.read_text()), cfg):
        (warnings if k.split(".")[0] in OPTIONAL_KEYS else problems).append(
            f"config.json is missing {k}: copy it from {EXAMPLE}")
    state_path = root / "state" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("kill"):
            warnings.append(f"kill switch is ON ({state.get('kill_reason') or 'no reason'}): only the user clears it")
        if state.get("halted"):
            warnings.append(f"buying is halted ({state.get('halt_reason')}): only the user resets it")
    git_warning = git_tracked_risk(root)
    if git_warning:
        warnings.append(git_warning)
    return {"ok": not problems, "data_dir": str(root), "account_number": cfg.get("account_number"),
            "problems": problems, "warnings": warnings}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd not in ("init", "doctor"):
        sys.exit(__doc__)
    result = init() if cmd == "init" else doctor()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
