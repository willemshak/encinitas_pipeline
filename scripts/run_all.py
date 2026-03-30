#!/usr/bin/env python3
"""Run all pipeline scripts in sequence."""

import subprocess
import sys
import time

from rich.console import Console

console = Console()

SCRIPTS = [
    ("01_crawl_index.py", "Crawl permit index page"),
    ("02_crawl_permit_pages.py", "Crawl individual permit pages"),
    ("03_extract_structured.py", "Extract structured data with Claude"),
    ("04_build_graph.py", "Build knowledge graph"),
]


def main():
    console.print("[bold]Encinitas Permit Pipeline — Full Run[/bold]\n")
    start = time.time()
    resume = "--resume" in sys.argv

    for script, description in SCRIPTS:
        console.print(f"\n{'='*60}")
        console.print(f"[bold cyan]{description}[/bold cyan] ({script})")
        console.print(f"{'='*60}\n")

        cmd = [sys.executable, f"scripts/{script}"]
        if resume and script in ("02_crawl_permit_pages.py", "03_extract_structured.py"):
            cmd.append("--resume")

        result = subprocess.run(cmd)

        if result.returncode != 0:
            console.print(f"\n[red bold]Pipeline failed at {script} (exit code {result.returncode})[/red bold]")
            sys.exit(result.returncode)

    elapsed = time.time() - start
    console.print(f"\n{'='*60}")
    console.print(f"[green bold]Pipeline complete![/green bold] Total time: {elapsed:.0f}s")
    console.print(f"{'='*60}")


if __name__ == "__main__":
    main()
