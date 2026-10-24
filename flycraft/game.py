"""Minecraft connection via Mineflayer (spec F05). Run: python -m flycraft.game

Finds an open LAN world (or uses a fixed host/port), creates a Mineflayer bot, and
waits for it to spawn. Kicks and errors before spawning end the run with a clear
message; after spawning, `kicked`/`end` mark the connection as lost.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import socket
import struct
import sys
import time

from . import check_env, config
from .config import Config

log = logging.getLogger("flycraft.game")

MINEFLAYER_VERSION = "4.39.0"           # pinned: newest release, supports up to MC 26.1.x
LAN_GROUP, LAN_PORT = "224.0.2.60", 4445  # vanilla "Open to LAN" multicast announcement
_LAN_AD = re.compile(r"\[MOTD\](.*?)\[/MOTD\]\[AD\](\d+)\[/AD\]")


class ConnectError(RuntimeError):
    pass


def _ipv4_interfaces() -> list[str]:
    addrs = {"0.0.0.0", "127.0.0.1"}
    try:
        addrs.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:  # the address used for the default route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET; no packet is sent
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(addrs)


def discover_lan(timeout: float) -> tuple[str, int, str]:
    """Listen for a LAN world announcement; returns (host, port, motd)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", LAN_PORT))
        # Join on every IPv4 interface: macOS only joins the default one for 0.0.0.0,
        # which misses announcements looped back from a game on this machine.
        for iface in _ipv4_interfaces():
            mreq = struct.pack("4s4s", socket.inet_aton(LAN_GROUP), socket.inet_aton(iface))
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, (host, _) = sock.recvfrom(1024)
            except socket.timeout:
                break
            if m := _LAN_AD.search(data.decode("utf-8", "replace")):
                return host, int(m.group(2)), m.group(1)
    finally:
        sock.close()
    raise ConnectError(f"no LAN world announced within {timeout:.0f} s; open your world to LAN "
                       "(Esc > Open to LAN) or set a fixed port in config.toml")


def chat_text(reason: str) -> str:
    """Plain text from a kick reason: a plain string or a JSON chat component."""
    try:
        component = json.loads(reason)
    except (ValueError, TypeError):
        return reason

    def flatten(c) -> str:
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(flatten(x) for x in c)
        if isinstance(c, dict):
            parts = [str(c.get("text", "")) or str(c.get("translate", ""))]
            parts += [flatten(x) for x in c.get("with", [])] + [flatten(x) for x in c.get("extra", [])]
            return " ".join(p for p in parts if p).strip()
        return str(c)

    return flatten(component) or reason


def _hint(reason: str) -> str:
    low = reason.lower()
    if "verif" in low or "authent" in low:
        return (" (the world is in online mode, which rejects auth = \"offline\" bots; use a server with "
                "online-mode=false, or auth = \"microsoft\" with a second account)")
    if "outdated" in low or "version" in low or "incompatible" in low:
        return " (check mc_version in config.toml matches the world)"
    if "econnrefused" in low:
        return " (nothing listening there; is the world open to LAN / the server running?)"
    return ""


class Connection:
    """A Mineflayer bot whose connection state is polled from bot.flycraft (see bot.js)."""

    def __init__(self, cfg: Config, host: str, port: int):
        from javascript import require

        mineflayer = require("mineflayer", MINEFLAYER_VERSION)
        self.helper = require("./bot.js")
        log.info("connecting to %s:%d as %s (mc %s, auth %s)", host, port, cfg.username, cfg.mc_version, cfg.auth)
        self.bot = self.helper.create(mineflayer, {"host": host, "port": port, "username": cfg.username,
                                                   "auth": cfg.auth, "version": cfg.mc_version})
        self._status = self.bot.flycraft
        self.reason: str | None = None

    @property
    def spawned(self) -> bool:
        return bool(self._status.spawned)

    def poll(self) -> bool:
        """Log new non-fatal warnings; returns False once the connection has ended."""
        warnings = str(self._status.takeWarnings() or "")
        for w in filter(None, warnings.splitlines()):
            log.warning("mineflayer error (continuing): %s", w)
        if self.reason is None and self._status.ended:
            kind, detail = str(self._status.kind), str(self._status.detail)
            self.reason = f"{kind}: {chat_text(detail) if kind == 'kicked' else detail}"
            log.error("%s%s", self.reason, _hint(self.reason))
        return self.reason is None

    def wait_for_spawn(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self.poll() and not self.spawned:
            if time.monotonic() > deadline:
                raise ConnectError(f"bot did not spawn within {timeout:.0f} s")
            time.sleep(0.1)
        if self.reason:
            raise ConnectError("could not join the world")

    def wait(self, seconds: float) -> bool:
        """Stay connected for `seconds`; returns False if the connection ended first."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self.poll():
                return False
            time.sleep(0.1)
        return True

    def quit(self) -> None:
        if self._status.ended:
            return
        try:
            self.bot.quit()
        except Exception as e:
            log.warning("bot.quit() failed: %s", e)
        deadline = time.monotonic() + 5
        while not self._status.ended and time.monotonic() < deadline:
            time.sleep(0.1)


def connect(cfg: Config) -> Connection:
    if cfg.port == 0:
        log.info("listening for a LAN world for up to %.0f s...", cfg.lan_discovery_timeout)
        host, port, motd = discover_lan(cfg.lan_discovery_timeout)
        log.info("found LAN world %r at %s:%d", motd, host, port)
    else:
        host, port = cfg.host, cfg.port
    conn = Connection(cfg, host, port)
    try:
        conn.wait_for_spawn(cfg.spawn_timeout)
    except ConnectError:
        conn.quit()
        raise
    pos = conn.bot.entity.position
    log.info("spawned as %s at (%.1f, %.1f, %.1f), game mode %s, version %s",
             conn.bot.username, pos.x, pos.y, pos.z, conn.bot.game.gameMode, conn.bot.version)
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Join Minecraft with a Mineflayer bot and report the spawn.")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    parser.add_argument("--host", help="override [minecraft] host")
    parser.add_argument("--port", type=int, help="override [minecraft] port (e.g. the LAN port shown in chat)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    missing = [name for name, problem in check_env.run_checks() if problem]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    cfg = config.load(args.config)
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    try:
        conn = connect(cfg)
    except ConnectError as e:
        log.error("%s", e)
        return 1
    try:
        log.info("staying in the world for %.0f s (Ctrl+C to leave early)", cfg.connect_test_seconds)
        if not conn.wait(cfg.connect_test_seconds):
            return 1
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        conn.quit()
    log.info("left the world cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
