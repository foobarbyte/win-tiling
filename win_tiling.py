#!/usr/bin/env python3
"""
Windows 10 style window tiling on Linux using Extended Window Manager Hints for the X Window System

Requires a python version that supports `from __future__ import annotations` (e.g. python 3.8+)

Dependencies:
    ewmh == 0.1.6
    screeninfo == 0.8

Example use:
    add `win_tiling.py server` to startup
    add keybindings via OS:
        super + h = `win_tiling.py client --use-hardcoded-port left`
        super + j = `win_tiling.py client --use-hardcoded-port down`
        super + k = `win_tiling.py client --use-hardcoded-port up`
        super + l = `win_tiling.py client --use-hardcoded-port right`
"""
from __future__ import annotations

import socket
from argparse import ArgumentParser
from contextlib import closing
from dataclasses import dataclass
from functools import partial
from multiprocessing import Process, Queue
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import cast, Dict, Callable, Iterator, Literal, NamedTuple, Tuple, Union

from ewmh import EWMH
from screeninfo import get_monitors
from screeninfo.common import Monitor


#############
# constants #
#############

PORT_FILE = '/tmp/win_tiling_port'
STOP_EVENT_HANDLER = 'win_tiling_end'
AUTHKEY = b'win_tiling'  # avoid *accidental* consumption of messages not intended for this program
DEFAULT_PORT = 6293  # hopefully doesn't colide with other applications
# https://en.wikipedia.org/wiki/List_of_TCP_and_UDP_port_numbers


#################
# window tiling #
#################

Up = Literal["up"]
Down = Literal["down"]
Left = Literal["left"]
Right = Literal["right"]
Direction = Union[Up, Down, Left, Right]
Corner = Tuple[Direction, Direction]
Point = Tuple[int, int]
Callback = Callable[[], None]


UP: Up = "up"
DOWN: Down = "down"
LEFT: Left = "left"
RIGHT: Right = "right"


opposites: Dict[Direction, Direction] = {
    LEFT: RIGHT,
    RIGHT: LEFT,
    UP: DOWN,
    DOWN: UP,
}


def corner(dir1: Direction, dir2: Direction) -> Corner:
    """Return a sorted tuple of directions, so that corner(UP, LEFT) == corner(LEFT, UP)
    """
    first, second = sorted((dir1, dir2))
    return first, second


cornerstates: Dict[Corner, Dict[Direction, Direction]] = {
    corner(LEFT, UP): {DOWN: LEFT, RIGHT: UP,},
    corner(RIGHT, UP): {DOWN: RIGHT, LEFT: UP,},
    corner(DOWN, LEFT): {UP: LEFT, RIGHT: DOWN,},
    corner(DOWN, RIGHT): {UP: RIGHT, LEFT: DOWN,},
}


def move_command(direction: Direction) -> None:
    """Issue the apropriate move command based on the user input and the current window's state
    """
    windowstate = get_windowstate()
    if windowstate is None:  # not tiled already
        return move(direction)
    if windowstate == direction:  # already tiled to this side
        return
    if windowstate == opposites[direction]:  # tiled to the opposite side
        return maximise()
    if windowstate in cornerstates:  # tiled to a quarter of the screen
        windowstate = cast(Corner, windowstate)  # TODO: replace cast with TypeGuard (python 3.10)
        directions = cornerstates[windowstate]
        move_direction = directions.get(direction, direction)
        return move(move_direction)
    # windowstate is not a cornerstate (or None) - it's a Direction
    # direction is not the same or opposite of the current windowstate
    # e.g. if direction is UP, then the current window is tiled either LEFT or RIGHT
    windowstate = cast(Direction, windowstate)  # TODO: replace cast with TypeGuard (python 3.10)
    return cornermove(corner(windowstate, direction))


def move(direction):
    """Tile to half of the screen.
    """
    debug(f"\t\tmove({direction})")
    screen = get_screen()
    pos = screen.anchors[direction]
    dim = screen.sizes[direction]
    _move(pos.x, pos.y, dim.w, dim.h)


def maximise():
    """Tile to the full screen size.
    """
    debug("\t\tmaximise()")
    screen = get_screen()
    _move(screen.x, screen.y, screen.w, screen.h)


def cornermove(corner_direction):
    """Tile to a quarter of the screen.
    """
    debug(f"\t\tsubmove({corner_direction})")
    screen = get_screen()
    x, y = screen.corneranchors[corner_direction]
    w, h = screen.cornersize
    _move(x, y, w, h)


def _move(x, y, w, h):
    """Actually move a window, taking decoration dimensions into account.
    """
    e = EWMH()
    win = e.getActiveWindow()
    e.setWmState(win, 0, '_NET_WM_STATE_MAXIMIZED_HORZ')
    e.setWmState(win, 0, '_NET_WM_STATE_MAXIMIZED_VERT')
    # unmaximize (even without flushing!) before getting decorations
    # to get correct decoration dimensions
    decoration_dimensions = get_decoration_dimensions()
    border_width = decoration_dimensions.w // 2
    new_x = x + border_width
    new_y = y + decoration_dimensions.h - border_width
    new_w = w - decoration_dimensions.w
    new_h = h - decoration_dimensions.h
    e.setMoveResizeWindow(win, x=new_x, y=new_y, w=new_w, h=new_h)
    e.display.flush()


class WH(NamedTuple):
    """Width and height pair.
    """
    w: int
    h: int


class XY(NamedTuple):
    """(x, y) coordinate pair.
    """
    x: int
    y: int


def get_decoration_dimensions() -> WH:
    """Get dimensions of window decorations (borders, title bar).
    """
    geometry = get_geometry()
    return WH(
        geometry.parent.width - geometry.window.width,
        geometry.parent.height - geometry.window.height,
    )


def get_windowstate() -> Direction | Corner | None:
    """Determine whether current window is tiled to half or quarter of the screen.
    """
    screen = get_screen()
    geometry = get_geometry().parent
    win_anchor = (geometry.x, geometry.y)
    win_size = (geometry.width, geometry.height)
    for state, anchor, size in screen.stateinfo():
        if (win_anchor, win_size) == (anchor, size):
            return state
    return None


@dataclass(init=False)
class Screen:
    """Store tiling information for given screen dimensions.

    A screen can have non-zero (x, y) coordinates, because individual monitor
    screens are treated as rectangular insets into a larger screen space.
    """
    def __init__(self, x: int, y: int, w: int, h: int):
        self.x, self.y = x, y
        self.w, self.h = w, h
        base_anchors: Dict[Direction, Point] = {
            LEFT: (0, 0),
            RIGHT: (w // 2, 0),
            UP: (0, 0),
            DOWN: (0, h // 2),
        }
        self.anchors: Dict[Direction, XY] = {
            direction: XY(x + x_anchor, y + y_anchor)
            for direction, (x_anchor, y_anchor)
            in base_anchors.items()
        }
        self.sizes: Dict[Direction, WH] = {
            LEFT: WH(w // 2, h),
            RIGHT: WH(w // 2, h),
            UP: WH(w, h // 2),
            DOWN: WH(w, h // 2),
        }
        self.corneranchors: Dict[Corner, XY] = {
            corner: XY(x + x_anchor, y + y_anchor)
            for corner, (x_anchor, y_anchor)
            in {
                corner(UP, LEFT): (0, 0),
                corner(UP, RIGHT): (w // 2, 0),
                corner(DOWN, LEFT): (0, h // 2),
                corner(DOWN, RIGHT): (w // 2, h // 2),
            }.items()
        }
        self.cornersize = WH(w // 2, h // 2)

    def stateinfo(self: Screen) -> Iterator[Tuple[Direction | Corner, XY, WH]]:
        """Iterate over known tilings (halves and quarters of screen).

        Yields three tuples containing:
            the tiling identifier (e.g. LEFT or corner(UP, LEFT)
            an (x, y) coordinate pair giving the top left point in the tiling
            a (width, height) pair giving the dimensions of the tiling
        """
        for direction, anchor in self.anchors.items():
            yield direction, anchor, self.sizes[direction]
        for corner_, anchor in self.corneranchors.items():
            yield corner_, anchor, self.cornersize


def get_screen() -> Screen:
    """Return Screen object for the monitor that displays the current window.
    """
    monitor = get_active_monitor()
    return Screen(
        x=monitor.x, y=monitor.y, w=monitor.width, h=monitor.height,
    )


def get_active_monitor() -> Monitor:
    """Return the monitor that displays the greatest portion of the current window.
    """
    geometry = get_geometry().parent
    return max(
        get_monitors(), key=partial(get_overlapping_area, geometry)
    )


def get_overlapping_area(r1: Rect, r2: Rect) -> int:
    """Return area of intersection between two `Rect`s
    """
    width = min(r1.x + r1.width, r2.x + r2.width) - max(r1.x, r2.x)
    height = min(r1.y + r1.height, r2.y + r2.height) - max(r1.y, r2.y)
    return max(width, 0) * max(height, 0)


@dataclass
class GeometryContainer:
    """Store `Rect`s for a window and and its parent.

    The parent contains the window's decorations as well as the window itself.
    """
    window: Rect
    parent: Rect


class Rect:
    """Store position and dimensions of a window, as well as miscellaneous metadata.

    The metadata comes from ewmh's inspection of the current window.
    """
    def __init__(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        **kwargs,
    ):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        for attr, value in kwargs.items():
            setattr(self, attr, value)


def get_geometry() -> GeometryContainer:
    """Return GeometryContainer with data about the current window.
    """
    window = EWMH().getActiveWindow()
    window_geometry = window.get_geometry()._data
    parent_geometry = window.query_tree().parent.get_geometry()._data
    return GeometryContainer(
        window=Rect(**window_geometry),
        parent=Rect(**parent_geometry),
    )


CALLBACKS: Dict[str, Callback] = {
    direction: partial(move_command, direction)
    for direction in opposites
}


###########
# sockets #
###########

@dataclass
class EventListener:
    """Enqueue events from a multiprocessing.Listener
    """
    listener: Listener

    def __post_init__(self):
        # pylint: disable=attribute-defined-outside-init
        self.queue: Queue[str] = Queue()

    def listen(self) -> None:
        while True:
            connection = self.listener.accept()
            event = connection.recv()
            if event == STOP_EVENT_HANDLER:
                return
            self.queue.put(event)


def get_free_port_number() -> int:
    """Ask the OS for an unused port number.
    """
    with closing(
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ) as sock:
        sock.bind(address(0))
        # port 0 = ask os for a random unused port
        sock.setsockopt(  # let us reuse the address
            socket.SOL_SOCKET,  # level
            socket.SO_REUSEADDR,  # optname
            1  # value (int)
        )
        _ip, port_number = sock.getsockname()
        return port_number


def get_port_from_file() -> int:
    return int(Path(PORT_FILE).read_text())


def address(port_number: int) -> Tuple[str, int]:
    """Return (ip/domain, port_number) pair.
    """
    return ('localhost', port_number)


def listen(port: int) -> Queue[str]:
    """Return a queue populated via listening to `port`.
    """
    try:
        listener = Listener(address(port), authkey=AUTHKEY)
    except OSError as error:
        raise error
    event_listener = EventListener(listener)
    proc = Process(target=event_listener.listen)
    proc.start()
    return event_listener.queue


def consume(queue: Queue[str], callbacks: Dict[str, Callback]) -> None:
    """Consume events from `queue`, calling callbacks[event] for each.
    """
    while True:
        event = queue.get()
        try:
            callback = callbacks[event]
        except KeyError:
            print("unhandled event", event)
        else:
            callback()


def send(msg: str, port: int) -> None:
    client = Client(address(port), authkey=AUTHKEY)
    client.send(msg)


#########
# debug #
#########

VERBOSE = False

def debug(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)


#######
# cli #
#######

def get_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Windows 10 style window tiling on Linux"
            " using Extended Window Manager Hints for the X Window System"
        ),
        epilog="Run server or client followed by -h flag to see documentation for their arguments",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="mode")
    subparsers.required = True
    server_subparser = subparsers.add_parser("server")
    server_port_args = server_subparser.add_mutually_exclusive_group()
    server_port_args.add_argument(
        "--port",
        type=int,
        help=f"Which port to listen on. Must be a free port. Defaults to `{DEFAULT_PORT}`.",
    )
    server_port_args.add_argument(
        "--random-port",
        action="store_true",
        help=(
            f"Use a randomly generated unused port number."
            f" Regardless of hwo the port used is arrived at,"
            f" the port number is written to `{PORT_FILE}`.",
        ),
    )
    client_subparser = subparsers.add_parser("client")
    client_subparser.add_argument(
        "message",
        choices=CALLBACKS,
        help=(
            f"Request the server to tile the current window as if WIN+direction had been pressed."
            f" The effect depends on the state of the current window."
            f" For example, WIN+left when the window is tiled right will maximise the window."
        ),
    )
    client_port_args = client_subparser.add_mutually_exclusive_group()
    client_port_args.add_argument(
        "--port",
        type=int,
        help=(
            f"Which port to use when communicating with the server."
            f" Must match the port the server is listening on."
        ),
    )
    client_port_args.add_argument(
        "--use-hardcoded-port",
        action="store_true",
        help=(
            f"Use the default port for the server, hardcoded in this script as `{DEFAULT_PORT}`."
            f" If neither --port nor --use-hardcoded-port are provided,"
            f" the port will be read from `{PORT_FILE}`."
        ),
    )
    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()
    global VERBOSE
    VERBOSE = args.verbose
    if args.mode == "server":
        port = (
            args.port
            if args.port is not None
            else get_free_port_number()
            if args.random_port
            else DEFAULT_PORT
        )
        Path(PORT_FILE).write_text(str(port))
        queue = listen(port)
        consume(queue, callbacks=CALLBACKS)
    elif args.mode == "client":
        port = (
            args.port
            if args.port is not None
            else DEFAULT_PORT
            if args.use_hardcoded_port
            else get_port_from_file()
        )
        send(args.message, port=port)


if __name__ == "__main__":
    main()
