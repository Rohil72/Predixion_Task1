"""Small terminal status UI used by the planner/search CLI."""

import sys
import time
from typing import List


class TerminalUI:
    def __init__(self, history_size: int = 10):
        self.history: List[str] = []
        self.history_size = history_size
        self._spinner = ["-", "|", "/", "."]

    def show_status(self, agent: str, title: str, spin_time: float = 0.4) -> None:
        line = f"[{agent}] -> {title}   "
        for s in self._spinner:
            sys.stderr.write("\r" + line + s)
            sys.stderr.flush()
            time.sleep(spin_time / len(self._spinner))

        sys.stderr.write("\r" + line + " \n")
        sys.stderr.flush()

        self.history.append(line)
        if len(self.history) > self.history_size:
            self.history.pop(0)

    def print_history(self) -> None:
        if not self.history:
            print("(no recent steps)", file=sys.stderr)
            return
        print("\nRecent steps:", file=sys.stderr)
        for h in self.history:
            print("  " + h, file=sys.stderr)
