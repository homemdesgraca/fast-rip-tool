from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.widgets import Footer, Header, Static

CommandRunner = Callable[[Sequence[str], Path], int]


@dataclass(frozen=True)
class RipperConfig:
    output_directory: Path
    country: str | None = None
    cover_art: str = "complete"
    max_retries: int = 5
    allow_unknown: bool = False
    allow_cdr: bool = False
    keep_going: bool = False
    track_template: str = "%A/%d/%N-%t - %n"
    disc_template: str = "%A/%d/%A - %d (disc %N)"

    def command(self) -> list[str]:
        command = [
            "whipper",
            "cd",
            "rip",
            "--prompt",
            "--output-directory",
            str(self.output_directory),
            "--track-template",
            self.track_template,
            "--disc-template",
            self.disc_template,
            "--cover-art",
            self.cover_art,
            "--max-retries",
            str(self.max_retries),
        ]
        if self.country:
            command.extend(("--country", self.country))
        if self.allow_unknown:
            command.append("--unknown")
        if self.allow_cdr:
            command.append("--cdr")
        if self.keep_going:
            command.append("--keep-going")
        return command

def get_drive_summary() -> str:
    executable = shutil.which("whipper")
    if not executable:
        return "whipper not found in PATH"
    try:
        output = subprocess.check_output(
            [executable, "drive", "list"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return " · ".join(lines) if lines else "No drive detected"
    except Exception as error:
        return f"Drive check error: {error}"


def run_command(command: Sequence[str], cwd: Path, *, pause_on_error: bool = False) -> int:
    code = subprocess.run(command, cwd=cwd, check=False).returncode
    if code != 0 and pause_on_error:
        try:
            input(f"\n[Whipper exited with status {code}. Press Enter to return to menu...]")
        except (EOFError, KeyboardInterrupt):
            pass
    return code

def changed_album_directories(root: Path, started_ns: int) -> list[Path]:
    albums: set[Path] = set()
    for track in root.rglob("*.flac"):
        try:
            if track.stat().st_mtime_ns >= started_ns:
                albums.add(track.parent)
        except OSError:
            continue
    return sorted(albums)


def open_in_picard(paths: Sequence[Path]) -> bool:
    executable = shutil.which("picard")
    if not executable or not paths:
        return False
    subprocess.Popen(
        [executable, *(str(path) for path in paths)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


class RipQueueApp(App[None]):
    TITLE = "CD Rip Queue"
    SUB_TITLE = "AccurateRip via whipper"
    CSS = """
    Screen {
        layout: vertical;
        min-width: 46;
        min-height: 14;
    }

    #stage {
        width: 1fr;
        height: 1fr;
        padding: 1 3;
    }

    #card {
        width: 100%;
        max-width: 88;
        height: auto;
        padding: 1 2;
    }

    #state {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        margin-bottom: 1;
    }

    #drive {
        color: $text-muted;
        margin-bottom: 1;
    }


    #detail {
        color: $text;
        margin-bottom: 1;
    }

    #output {
        color: $text-muted;
        margin-bottom: 1;
    }

    #hint {
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("enter", "rip", "Rip next disc", priority=True),
        Binding("p", "open_picard", "Open in Picard"),
        Binding("r", "reset", "Ready"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        config: RipperConfig,
        runner: CommandRunner = run_command,
        *,
        handoff_terminal: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.runner = runner
        self.handoff_terminal = handoff_terminal
        self.ripping = False
        self.completed = 0
        self.last_albums: list[Path] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Center(id="stage"):
            with Vertical(id="card"):
                yield Static("READY", id="state")
                yield Static(
                    "Insert a CD, close the tray, then press Enter.", id="detail"
                )
                yield Static(id="drive")
                yield Static(id="output")
                yield Static(
                    "Whipper will temporarily take over the terminal so its "
                    "MusicBrainz prompts remain usable.",
                    id="hint",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._show_ready()

    def _set_text(self, selector: str, value: str, *, style: str = "") -> None:
        self.query_one(selector, Static).update(Text(value, style=style))

    def _show_ready(self) -> None:
        self._set_text("#state", "READY", style="bold cyan")
        self._set_text(
            "#detail", "Insert a CD, close the tray, then press Enter."
        )
        self._set_text("#drive", f"Drive: {get_drive_summary()}", style="dim")
        summary = f"Destination: {self.config.output_directory}"
        if self.completed:
            summary += f"\nCompleted this session: {self.completed} disc(s)"
        self._set_text("#output", summary, style="dim")
    def action_reset(self) -> None:
        if not self.ripping:
            self._show_ready()

    def action_rip(self) -> None:
        if not self.ripping:
            self._rip_disc()

    @work(exclusive=True)
    async def _rip_disc(self) -> None:
        self.ripping = True
        self._set_text("#state", "STARTING", style="bold yellow")
        self._set_text(
            "#detail", "Handing the terminal to whipper. Follow its prompts."
        )
        started_ns = time.time_ns()
        command = self.config.command()
        try:
            if self.handoff_terminal:
                with self.suspend():
                    return_code = await asyncio.to_thread(
                        self.runner,
                        command,
                        self.config.output_directory,
                        pause_on_error=True,
                    )
            else:
                return_code = await asyncio.to_thread(
                    self.runner, command, self.config.output_directory
                )
        except Exception as error:
            self._set_text("#state", "ERROR", style="bold red")
            self._set_text("#detail", f"Could not start whipper: {error}")
            self._set_text("#output", "Press Enter to retry or q to quit.", style="dim")
        else:
            if return_code == 0:
                self.completed += 1
                self.last_albums = changed_album_directories(
                    self.config.output_directory, started_ns
                )
                self._set_text("#state", "COMPLETE", style="bold green")
                self._set_text(
                    "#detail",
                    "Rip completed. Replace the disc and press Enter for the next one.",
                )
                if self.last_albums:
                    albums = "\n".join(str(path) for path in self.last_albums)
                    self._set_text(
                        "#output",
                        f"New album folder(s):\n{albums}\n\nPress p to review in Picard.",
                        style="dim",
                    )
                else:
                    self._set_text(
                        "#output",
                        "No newly written FLAC files were detected. Press Enter to continue.",
                        style="dim",
                    )
            else:
                self._set_text("#state", "FAILED", style="bold red")
                self._set_text(
                    "#detail", f"Whipper exited with status {return_code}."
                )
                self._set_text(
                    "#output", "Check the output above, then press Enter to retry.", style="dim"
                )
        finally:
            self.ripping = False

    def action_open_picard(self) -> None:
        if self.ripping:
            return
        if open_in_picard(self.last_albums):
            self.notify("Opened the latest album in Picard")
        elif not self.last_albums:
            self.notify("Rip a disc first", severity="warning")
        else:
            self.notify("Picard was not found", severity="error")


def run_plain(config: RipperConfig) -> int:
    completed = 0
    last_albums: list[Path] = []
    while True:
        choice = input("[Enter] rip next disc  [p] Picard  [q] quit: ").strip().lower()
        if choice == "q":
            return 0
        if choice == "p":
            if not open_in_picard(last_albums):
                print("No completed album is available, or Picard was not found.")
            continue
        if choice:
            print("Unknown choice. Press Enter, p, or q.")
            continue

        started_ns = time.time_ns()
        return_code = run_command(config.command(), config.output_directory, pause_on_error=False)
        if return_code != 0:
            print(f"Whipper failed with status {return_code}.", file=sys.stderr)
            continue
        completed += 1
        last_albums = changed_album_directories(config.output_directory, started_ns)
        print(f"Completed {completed} disc(s).")
        for album in last_albums:
            print(f"Album: {album}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rip a sequential queue of CDs with whipper."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd(),
        help="music library root (default: current directory)",
    )
    parser.add_argument("--country", help="prefer MusicBrainz releases from COUNTRY")
    parser.add_argument(
        "--cover-art",
        choices=("file", "embed", "complete"),
        default="complete",
        help="cover-art handling (default: file plus embedded)",
    )
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--allow-unknown", action="store_true")
    parser.add_argument("--allow-cdr", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument(
        "--track-template",
        default="%A/%d/%N-%t - %n",
        help="whipper track path template",
    )
    parser.add_argument(
        "--disc-template",
        default="%A/%d/%A - %d (disc %N)",
        help="whipper cue/log/playlist path template",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="use a plain sequential prompt",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check optical drive and disc status, then exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        print("Optical drive check:")
        print(f"  {get_drive_summary()}")
        return 0

    output = args.output.expanduser().resolve()
    if args.max_retries < 0:
        print("--max-retries must be zero or greater", file=sys.stderr)
        return 2
    if not shutil.which("whipper"):
        print("whipper was not found in PATH", file=sys.stderr)
        return 127
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"cannot use output directory {output}: {error}", file=sys.stderr)
        return 2

    config = RipperConfig(
        output_directory=output,
        country=args.country,
        cover_art=args.cover_art,
        max_retries=args.max_retries,
        allow_unknown=args.allow_unknown,
        allow_cdr=args.allow_cdr,
        keep_going=args.keep_going,
        track_template=args.track_template,
        disc_template=args.disc_template,
    )
    if args.no_tui or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return run_plain(config)

    app = RipQueueApp(config)
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
