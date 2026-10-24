"""Raise OpenMinion's effective per-request provider timeout.

Finding (BUILDING_BLOCKS.md #8): the transport timeout comes from the
top-level `providers` section of agents.json (default 60s), NOT from the
per-agent provider_config_overrides. Local CPU models need much more
than 60s for a ~2k-token prefill, so raise it here.

Usage: python3 scripts/set_timeout.py [seconds] [provider]
"""
import json
import sys
from pathlib import Path

seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 900
provider = sys.argv[2] if len(sys.argv) > 2 else "openai"
path = Path(".openminion/agents.json")
cfg = json.loads(path.read_text())
cfg["providers"][provider]["timeout_seconds"] = seconds
path.write_text(json.dumps(cfg, indent=2))
print(f"providers.{provider}.timeout_seconds = {seconds}")
