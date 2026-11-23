from __future__ import annotations

import sys
from pathlib import Path

from scraper.config import ConfigError, load_config, write_public_config


def main() -> None:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    dest = Path(sys.argv[2] if len(sys.argv) > 2 else "site/data/config.json")
    try:
        config = load_config(source)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    write_public_config(config, dest)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
