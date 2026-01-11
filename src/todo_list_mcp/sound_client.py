"""Cross-platform sound playback client.

Provides a thread-safe API to create, read, update, and delete simple sound
playback tasks on Windows, macOS, and Linux. Playback runs on a background
worker thread and marshals all requests through an internal queue.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

WorkerRequest = tuple[
    Callable[..., object],
    tuple[object, ...],
    dict[str, object],
    queue.Queue[tuple[bool, object]],
]


def get_default_sound_path() -> Optional[str]:
    """Get the path to the default reminder sound.

    Returns:
        Path to the default sound file if it exists, None otherwise.
    """
    # Try to find the bundled sound file
    package_dir = Path(__file__).parent
    sound_path = package_dir / "assets" / "reminder_chime.wav"

    if sound_path.exists():
        return str(sound_path)

    logger.warning(
        "Default sound file not found",
        extra={"component": "sound_client", "path": str(sound_path)},
    )
    return None


@dataclass
class _PlaybackHandle:
    kind: str
    proc: Optional[subprocess.Popen] = None
    player: Optional[str] = None

    def stop(self) -> None:
        """Attempt to stop playback for this handle."""
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            return

        if self.kind == "winsound":  # pragma: no cover - platform specific
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                # Best-effort stop; ignore failures
                pass


@dataclass
class Sound:
    sound_id: str
    source: Optional[str]
    loop: bool
    interval_seconds: float
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    next_at: float = field(default_factory=time.time)
    active: bool = True


class SoundClient:
    def __init__(self, poll_interval_ms: int = 80) -> None:
        self._poll_interval_s = max(0.016, poll_interval_ms / 1000.0)
        self._queue: queue.Queue[WorkerRequest] = queue.Queue()
        self._shutdown_event = threading.Event()
        self._ready = threading.Event()
        self._sounds: Dict[str, Sound] = {}
        self._playbacks: Dict[str, List[_PlaybackHandle]] = {}
        self._playback_lock = threading.Lock()

        self._worker = threading.Thread(
            target=self._run_worker, name="SoundWorker", daemon=True
        )
        logger.debug("Starting SoundClient worker thread")
        self._worker.start()
        if not self._ready.wait(timeout=5):
            logger.error("Sound worker thread failed to start", extra={"timeout": 5})
            raise RuntimeError("Sound worker thread did not start")
        logger.info(
            "SoundClient initialized", extra={"poll_interval_ms": poll_interval_ms}
        )

    def create_sound(
        self,
        source: Optional[str] = None,
        *,
        loop: bool = False,
        interval_seconds: float = 5.0,
    ) -> str:
        # If no source is provided, use the default sound
        if source is None:
            source = get_default_sound_path()

        interval_seconds = max(0.25, float(interval_seconds))
        sound_id = str(uuid.uuid4())
        sound = Sound(
            sound_id=sound_id,
            source=source,
            loop=loop,
            interval_seconds=interval_seconds,
            next_at=time.time(),
        )
        self._call_worker(self._add_sound, sound)
        logger.info(
            "Created sound",
            extra={
                "action": "create",
                "sound_id": sound_id[:8],
                "source": source,
                "loop": loop,
                "interval_seconds": interval_seconds,
            },
        )
        return sound_id

    def update_sound(
        self,
        sound_id: str,
        *,
        source: Optional[str] = None,
        loop: Optional[bool] = None,
        interval_seconds: Optional[float] = None,
    ) -> None:
        if interval_seconds is not None:
            interval_seconds = max(0.25, float(interval_seconds))
        self._call_worker(self._update_sound, sound_id, source, loop, interval_seconds)
        logger.info(
            "Updated sound",
            extra={
                "action": "update",
                "sound_id": sound_id[:8],
                "source": source,
                "loop": loop,
                "interval_seconds": interval_seconds,
            },
        )

    def delete_sound(self, sound_id: str) -> None:
        self._call_worker(self._delete_sound, sound_id)
        logger.info(
            "Deleted sound", extra={"action": "delete", "sound_id": sound_id[:8]}
        )

    def stop_sound(self, sound_id: str) -> None:
        """Stop playback immediately and deactivate the sound."""
        self._call_worker(self._stop_sound, sound_id)
        logger.info("Stopped sound", extra={"action": "stop", "sound_id": sound_id[:8]})

    def list_sounds(self) -> List[dict]:
        return self._call_worker(self._snapshot_sounds)

    def shutdown(self) -> None:
        if self._shutdown_event.is_set():
            logger.debug("Shutdown already in progress", extra={"action": "shutdown"})
            return
        logger.info("Shutting down SoundClient", extra={"action": "shutdown"})
        self._shutdown_event.set()
        try:
            self._call_worker(lambda: None, allow_after_shutdown=True)
        except Exception as exc:
            logger.warning(
                "Error during shutdown", extra={"action": "shutdown", "error": str(exc)}
            )
        self._stop_all_playbacks()
        self._worker.join(timeout=2)
        self._stop_all_playbacks()
        logger.info("SoundClient shutdown complete", extra={"action": "shutdown"})

    # Internal worker helpers

    def _run_worker(self) -> None:
        logger.debug("Worker thread started")
        self._ready.set()
        while not self._shutdown_event.is_set():
            try:
                self._process_request(*self._queue.get(timeout=self._poll_interval_s))
            except queue.Empty:
                pass
            self._tick_sounds()
        # drain pending requests
        while True:
            try:
                self._process_request(*self._queue.get_nowait())
            except queue.Empty:
                break

    def _process_request(
        self,
        func: Callable[..., object],
        args: tuple[object, ...],
        kwargs: dict[str, object],
        response_q: queue.Queue[tuple[bool, object]],
    ) -> None:
        try:
            result = func(*args, **kwargs)
            response_q.put((True, result))
        except Exception as exc:  # pragma: no cover - surfaced to caller
            response_q.put((False, exc))

    def _call_worker(
        self,
        func: Callable[..., object],
        *args: object,
        allow_after_shutdown: bool = False,
        **kwargs: object,
    ) -> Any:
        if self._shutdown_event.is_set() and not allow_after_shutdown:
            raise RuntimeError("Sound client is shut down")
        response_q: queue.Queue[tuple[bool, object]] = queue.Queue()
        self._queue.put((func, args, kwargs, response_q))
        try:
            success, payload = response_q.get(timeout=5)
        except queue.Empty as exc:
            raise RuntimeError("Sound worker unresponsive") from exc
        if success:
            return payload
        raise payload

    def _add_sound(self, sound: Sound) -> None:
        self._mark_active(sound)
        self._sounds[sound.sound_id] = sound

    def _update_sound(
        self,
        sound_id: str,
        source: Optional[str],
        loop: Optional[bool],
        interval_seconds: Optional[float],
    ) -> None:
        sound = self._sounds.get(sound_id)
        if not sound:
            logger.error(
                "Attempted to update unknown sound",
                extra={"component": "sound_worker", "sound_id": sound_id[:8]},
            )
            raise KeyError(f"Unknown sound: {sound_id}")
        if source is not None:
            sound.source = source
        if loop is not None:
            sound.loop = loop
        if interval_seconds is not None:
            sound.interval_seconds = max(0.25, float(interval_seconds))
        self._mark_active(sound)

    def _mark_active(self, sound: Sound, *, next_at: Optional[float] = None) -> None:
        now = time.time()
        sound.updated_at = now
        sound.next_at = next_at if next_at is not None else now
        sound.active = True

    def _delete_sound(self, sound_id: str) -> None:
        sound = self._sounds.pop(sound_id, None)
        if sound:
            sound.active = False
            self._stop_playbacks(sound_id)

    def _stop_sound(self, sound_id: str) -> None:
        sound = self._sounds.get(sound_id)
        if not sound:
            logger.error(
                "Attempted to stop unknown sound",
                extra={"component": "sound_worker", "sound_id": sound_id[:8]},
            )
            raise KeyError(f"Unknown sound: {sound_id}")
        sound.active = False
        self._stop_playbacks(sound_id)

    def _snapshot_sounds(self) -> List[dict]:
        now = time.time()
        return [
            {
                "id": s.sound_id,
                "source": s.source,
                "loop": s.loop,
                "interval_seconds": s.interval_seconds,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "active": s.active,
                "next_in_seconds": max(0.0, s.next_at - now),
            }
            for s in self._sounds.values()
        ]

    def _register_playback(self, sound_id: str, handle: _PlaybackHandle) -> None:
        with self._playback_lock:
            self._playbacks.setdefault(sound_id, []).append(handle)

    def _unregister_playback(self, sound_id: str, handle: _PlaybackHandle) -> None:
        with self._playback_lock:
            handles = self._playbacks.get(sound_id)
            if not handles:
                return
            try:
                handles.remove(handle)
            except ValueError:
                pass
            if not handles:
                self._playbacks.pop(sound_id, None)

    def _stop_playbacks(self, sound_id: str) -> None:
        handles: List[_PlaybackHandle]
        with self._playback_lock:
            handles = self._playbacks.pop(sound_id, [])
        for handle in handles:
            handle.stop()
        self._stop_windows_playback()

    def _stop_all_playbacks(self) -> None:
        with self._playback_lock:
            items = list(self._playbacks.items())
            self._playbacks.clear()
        for sound_id, handles in items:
            for handle in handles:
                handle.stop()
        self._stop_windows_playback()

    def _stop_windows_playback(self) -> None:  # pragma: no cover - platform specific
        if not sys.platform.startswith("win"):
            return
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def _tick_sounds(self) -> None:
        now = time.time()
        for sound in self._sounds.values():
            if not sound.active:
                continue
            if sound.next_at <= now:
                self._start_playback(sound)
                if sound.loop:
                    sound.next_at = now + sound.interval_seconds
                    sound.updated_at = now
                else:
                    sound.active = False

    def _start_playback(self, sound: Sound) -> None:
        thread = threading.Thread(target=self._play_sound, args=(sound,), daemon=True)
        thread.start()

    def _play_sound(self, sound: Sound) -> None:
        source = sound.source
        logger.debug(
            "Attempting to play sound",
            extra={
                "component": "sound_playback",
                "sound_id": sound.sound_id[:8],
                "source": source,
                "platform": sys.platform,
            },
        )
        if not (source and os.path.isfile(source)):
            if source:
                logger.warning(
                    "Sound file not found",
                    extra={"component": "sound_playback", "source": source},
                )
            self._beep()
            return

        handle: Optional[_PlaybackHandle] = None
        try:
            if sys.platform.startswith("win"):
                handle = self._play_windows(source)
            elif sys.platform == "darwin":
                handle = self._play_process(["afplay", source])
            else:
                handle = self._play_linux(source)

            if handle:
                self._register_playback(sound.sound_id, handle)
                if handle.proc:
                    handle.proc.wait()
        finally:
            if handle:
                self._unregister_playback(sound.sound_id, handle)

    def _play_windows(
        self, source: str
    ) -> Optional[_PlaybackHandle]:  # pragma: no cover - platform specific
        try:
            import winsound

            winsound.PlaySound(source, winsound.SND_FILENAME | winsound.SND_ASYNC)
            logger.debug(
                "Playing sound",
                extra={
                    "component": "sound_playback",
                    "player": "winsound",
                    "source": source,
                },
            )
            return _PlaybackHandle(kind="winsound", player="winsound")
        except Exception as exc:
            logger.warning(
                "Failed to play sound",
                extra={
                    "component": "sound_playback",
                    "player": "winsound",
                    "error": str(exc),
                },
            )
            self._beep()
            return None

    def _play_linux(self, source: str) -> Optional[_PlaybackHandle]:
        for cmd in (
            ["paplay", source],
            ["aplay", source],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", source],
        ):
            if shutil.which(cmd[0]):
                logger.debug(
                    "Playing sound",
                    extra={
                        "component": "sound_playback",
                        "player": cmd[0],
                        "source": source,
                    },
                )
                return self._play_process(cmd)
        logger.warning(
            "No audio player found, falling back to beep",
            extra={"component": "sound_playback", "platform": "linux"},
        )
        self._beep()
        return None

    def _play_process(self, cmd: List[str]) -> Optional[_PlaybackHandle]:
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return _PlaybackHandle(kind="process", proc=proc, player=cmd[0])
        except Exception as exc:
            logger.error(
                "Failed to execute player",
                extra={
                    "component": "sound_playback",
                    "player": cmd[0],
                    "error": str(exc),
                },
            )
            self._beep()
            return None

    def _beep(self) -> None:
        try:
            logger.debug(
                "Playing system beep",
                extra={"component": "sound_playback", "player": "system_beep"},
            )
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception as exc:
            logger.error(
                "Failed to play beep",
                extra={
                    "component": "sound_playback",
                    "player": "system_beep",
                    "error": str(exc),
                },
            )


# Example usage and testing


def main() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
            "<level>{message}</level> | {extra}"
        ),
        level="DEBUG",
    )

    print("=== Sound Client Example ===")
    print("Platform:", sys.platform)
    print()

    default_sound = get_default_sound_path()
    client = SoundClient()
    try:
        print(f"Default sound: {default_sound}")
        print("\nTest 1: Playing default sound once...")
        sound_id = client.create_sound(loop=False)
        print("Created sound", sound_id)
        time.sleep(2)

        print("\nTest 2: Updating to loop every 2 seconds...")
        client.update_sound(sound_id, loop=True, interval_seconds=2.0)
        print("Updated sound to loop every 2s")
        print("(You should hear the sound every 2 seconds)")
        time.sleep(4)

        print("\nTest 3: Stopping mid-playback...")
        client.stop_sound(sound_id)
        print("Stopped sound; it should halt immediately")
        time.sleep(1)

        print("\nCurrent sounds:", client.list_sounds())

        client.delete_sound(sound_id)
        print("\nDeleted sound")
        time.sleep(1)

        print("\n--- Usage Notes ---")
        print("Default sound is used automatically when no source is specified:")
        print("  client.create_sound(loop=False)")
        print("\nTo play a custom sound file:")
        print("  client.create_sound(source='/path/to/sound.wav', loop=False)")
        print("\nSupported formats vary by platform:")
        print("  Linux: WAV (paplay/aplay) or any format with ffplay")
        print("  macOS: WAV, MP3, AIFF, etc. (afplay)")
        print("  Windows: WAV (winsound)")

    finally:
        client.shutdown()
        print("\nShutdown complete")


if __name__ == "__main__":
    main()
