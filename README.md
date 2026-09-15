# fast-rip-tool

Sequential CD ripping queue with a terminal UI frontend for whipper and MusicBrainz Picard.

## Requirements

System packages:
- whipper
- picard (optional, for metadata review)
- python >= 3.12

## Setup

Using uv:

```bash
uv sync
```

Or using pip:

```bash
pip install -r requirements.txt
```

## Usage

Start the interactive queue:

```bash
uv run cd-rip-queue
```

Check optical drive detection and configured read offset:

```bash
uv run cd-rip-queue --check
```

Run in plain prompt mode without the full-screen TUI:

```bash
uv run cd-rip-queue --no-tui
```

## Keybindings

- Enter: Rip inserted CD
- p: Open most recently ripped album in Picard
- r: Reset view to ready state
- q: Quit
