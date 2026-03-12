"""CLI entry point for Horizon."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from .storage.manager import StorageManager
from .orchestrator import HorizonOrchestrator


console = Console()


def _setup_logging() -> None:
    """Configure file-based logging (INFO level, minute precision)."""
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"horizon-{datetime.now().strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )


def print_banner():
    """Print the application banner."""
    banner = r"""
[bold blue]
  _    _            _
 | |  | |          (_)
 | |__| | ___  _ __ _ ___  ___  _ __
 |  __  |/ _ \| '__| |_  / / _ \| '_ \
 | |  | | (_) | |  | |/ / | (_) | | | |
 |_|  |_|\___/|_|  |_/___| \___/|_| |_|
[/bold blue]
[cyan]  AI-Driven Information Aggregation System[/cyan]
    """
    console.print(banner)


def main():
    """Main CLI entry point."""
    print_banner()

    parser = argparse.ArgumentParser(description="Horizon - AI-Driven Information Aggregation System")
    parser.add_argument("--hours", type=int, help="Force fetch from last N hours")
    parser.add_argument(
        "--from-cache", action="store_true",
        help="Skip fetch/score/enrich, regenerate outputs from today's cached items",
    )
    args = parser.parse_args()

    try:
        load_dotenv()
        _setup_logging()

        # Ensure we're in the project directory or use data/ in current dir
        data_dir = Path("data")

        # Initialize storage manager
        storage = StorageManager(data_dir=str(data_dir))

        # Load configuration
        try:
            config = storage.load_config()
        except FileNotFoundError:
            console.print("[bold red]❌ Configuration file not found![/bold red]\n")
            console.print("Please create [cyan]data/config.json[/cyan] based on the template:\n")
            print_config_template()
            sys.exit(1)
        except Exception as e:
            console.print(f"[bold red]❌ Error loading configuration: {e}[/bold red]")
            sys.exit(1)

        # Create and run orchestrator
        orchestrator = HorizonOrchestrator(config, storage)
        asyncio.run(orchestrator.run(force_hours=args.hours, from_cache=args.from_cache))

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]❌ Fatal error: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def print_config_template():
    """Print configuration template from config.example.json."""
    example_path = Path("data/config.example.json")
    if example_path.exists():
        console.print(example_path.read_text(encoding="utf-8"))
    else:
        console.print("[yellow]data/config.example.json not found.[/yellow]")
    console.print(
        "\nAlso create a .env file with:\n"
        "ANTHROPIC_API_KEY=your_api_key_here\n"
        "GITHUB_TOKEN=your_github_token_here (optional but recommended)\n"
        "WXPUSHER_APP_TOKEN=your_wxpusher_token (optional)\n"
        "WXPUSHER_UIDS=UID_xxx (optional)\n"
    )


if __name__ == "__main__":
    main()
