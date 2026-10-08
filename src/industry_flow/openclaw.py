from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import settings


def send_openclaw_message(message: str, media_path: Path | None = None) -> None:
    if not settings.openclaw_ready:
        raise RuntimeError(
            "OpenClaw delivery requires both OPENCLAW_CHANNEL and OPENCLAW_TARGET"
        )

    command = [
        settings.openclaw_bin,
        "message",
        "send",
        "--channel",
        settings.openclaw_channel,
        "--target",
        settings.openclaw_target,
        "--message",
        message,
    ]
    if settings.openclaw_account:
        command.extend(["--account", settings.openclaw_account])
    if media_path is not None:
        if not media_path.is_file():
            raise FileNotFoundError(f"OpenClaw media file does not exist: {media_path}")
        command.extend(["--media", str(media_path.resolve())])

    environment = None
    executable_path = shutil.which(settings.openclaw_bin)
    if executable_path:
        command[0] = executable_path

    if (
        os.name == "nt"
        and executable_path
        and Path(executable_path).suffix.lower() in {".cmd", ".bat"}
    ):
        # npm exposes OpenClaw as a .cmd shim on Windows. Route its arguments
        # through a fixed PowerShell script and pass values through the
        # environment so report text is never interpolated into shell code.
        environment = os.environ.copy()
        environment["QUANT_GUY_OPENCLAW_BIN"] = executable_path
        environment["QUANT_GUY_OPENCLAW_ARGS"] = json.dumps(
            command[1:], ensure_ascii=False
        )
        command = [
            shutil.which("powershell.exe") or "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$openclawArgs = @(ConvertFrom-Json -InputObject "
                "$env:QUANT_GUY_OPENCLAW_ARGS); "
                "& $env:QUANT_GUY_OPENCLAW_BIN @openclawArgs; "
                "exit $LASTEXITCODE"
            ),
        ]

    try:
        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.openclaw_timeout,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"OpenClaw CLI was not found: {settings.openclaw_bin}; "
            "set OPENCLAW_BIN to its executable path"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"OpenClaw delivery timed out after {settings.openclaw_timeout} seconds"
        ) from None
    except OSError as exc:
        raise RuntimeError(f"Could not start OpenClaw CLI: {exc}") from None

    if result.returncode != 0:
        raise RuntimeError(
            f"OpenClaw message send failed (exit={result.returncode}); "
            "check the OpenClaw channel configuration and CLI logs"
        )
