#!/bin/bash
# Double-click this file to start the WSTI Quiz on a Mac.
cd "$(dirname "$0")"
clear
echo "Starting WSTI Quiz..."
echo
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed yet."
  echo "A small install window may pop up - click Install, then double-click this file again."
  xcode-select --install 2>/dev/null
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi
python3 WSTI_Quiz.py
echo
read -n 1 -s -r -p "Quiz stopped. Press any key to close this window..."
