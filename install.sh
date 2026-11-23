#!/bin/sh
# Symlink the imessage skill into Claude Code (and Codex, if installed).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)/skill/imessage"
chmod +x "$SRC/imsg.py"
for d in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
  [ -d "$(dirname "$d")" ] || continue
  mkdir -p "$d"
  if [ -e "$d/imessage" ] && [ ! -L "$d/imessage" ]; then
    echo "skip $d/imessage (exists and is not a symlink)"; continue
  fi
  ln -sfn "$SRC" "$d/imessage" && echo "linked $d/imessage -> $SRC"
done
