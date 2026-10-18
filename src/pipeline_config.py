"""
Load config/config.yaml once and expose its folders as absolute Paths.

Paths in the config are relative to the repo root, so scripts work no matter
which folder they are run from.

    from pipeline_config import PATHS
    PATHS["interim"] / "c1_index_raw.csv"
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"

with open(CONFIG_FILE, encoding="utf-8") as fh:
    CONFIG = yaml.safe_load(fh) or {}

PATHS = {k: ROOT / v for k, v in (CONFIG.get("paths") or {}).items()}
