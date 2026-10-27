"""Where the data dir (config.json, state/, run/, journal/, reports/) lives. Pure stdlib.

Order: an explicit path (e.g. report.py --root), then $AGENT_TRADER_HOME, then the current
directory if it has a config.json, else the project root: the git top-level of the current
directory, or the current directory itself outside a git repo. So the default is the folder
the agent runs in, and the scripts still find it from a subfolder.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def project_root(start=None):
    cwd = Path(start or Path.cwd()).resolve()
    if (cwd / "config.json").exists():
        return cwd
    try:
        top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        top = ""
    return Path(top).resolve() if top else cwd


def resolve(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("AGENT_TRADER_HOME"):
        return Path(os.environ["AGENT_TRADER_HOME"]).expanduser().resolve()
    return project_root()


def require_config(root):
    """Exit with a setup hint if root has no config.json; else return the parsed config."""
    cfg = root / "config.json"
    if not cfg.exists():
        sys.exit(f"no config.json in {root}: run `python3 {Path(__file__).with_name('setup.py')} init "
                 "--account <ACCOUNT_NUMBER>` from the project folder (or point AGENT_TRADER_HOME at your data dir)")
    return json.loads(cfg.read_text())
