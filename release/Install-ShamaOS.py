#!/usr/bin/env python3
"""The entry script distributed in ShamaOS-installer.zip."""
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here / "src"))
try:
    from shamaos.installer import main
except ModuleNotFoundError:
    print("Run Start-Windows.cmd or Start-Mac-Linux.command to install the small helper first.")
    raise SystemExit(1)

if __name__ == "__main__":
    # Downloads may sit beside the extracted kit or inside it.
    downloads = here if any(here.glob("ShamaOS-world.zip*")) else here.parent
    raise SystemExit(main(["--downloads", str(downloads), *sys.argv[1:]]))
