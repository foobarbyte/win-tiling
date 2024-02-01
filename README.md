# win-tiling
Windows 10 style window tiling on Linux using Extended Window Manager Hints for the X Window System

This means that window tiling is stateful, and how the input is processed depends on the current state of the active window.

There are four valid inputs: up, down, left, and right.
If the current window isn't tiled, then it will tile to the appropriate half of the screen.
If it is already tiled to half of the screen, then tiling in the opposite direction will maximise it.
Tiling in a perpendicular direction will tile to a quarter of the screeen.
When tiled into a quarter of the screen, subsequent inputs will result in tiling to half of the screen.

# usage

Requires a python version that supports `from __future__ import annotations` (e.g. python 3.8+)

Dependencies:

```
ewmh == 0.1.6
screeninfo == 0.8
```

Run `win_tiling.py --help` for commandline documentation.

Example use:
- add `win_tiling.py server` to startup
- add keybindings via OS:

| keys      | command                                           |
|-----------|---------------------------------------------------|
| `WIN + h` | `win_tiling.py client --use-hardcoded-port left`  |
| `WIN + j` | `win_tiling.py client --use-hardcoded-port down`  |
| `WIN + k` | `win_tiling.py client --use-hardcoded-port up`    |
| `WIN + l` | `win_tiling.py client --use-hardcoded-port right` |

Performance could be improved, but the tiling is accurate!

# TODO
- tiling into thirds of the screen (requires additional inputs?)
- tile onto adjacent monitors (eg: WIN+right, WIN+right should result first right tiling on the current monitor, and then left tiling on the monitor to the right).
- additional inputs to move windows to different monitors while preserving their tiling state, even if the monitors have different dimensions
- option to have down result in minimisation when it would under Windows
- experiment with improving performance (Rust rewrite incoming?)
