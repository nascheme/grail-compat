#!/usr/bin/env -S uv run --locked
"""Build the static site and publish unchanged Markdown beside each HTML page."""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path


def main():
    os.chdir(Path(__file__).resolve().parent)
    subprocess.run(
        ["uvx", "zensical", "build", "--clean"], check=True
    )


if __name__ == "__main__":
    main()
