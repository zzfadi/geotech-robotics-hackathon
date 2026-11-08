"""Prepare OXRSys capabilities before it creates the Mac OpenXR instance."""
import os
from pathlib import Path
import re
import tempfile
import tomllib


def prepare_runtime(path: Path) -> None:
    """Preserve the runtime profile, including comments and unrelated tables."""
    before = path.read_text() if path.exists() else None
    original = before or ""
    config = tomllib.loads(original)
    keys = ("passthrough_enabled", "app_alpha_blend_passthrough")
    if all(config.get("streaming", {}).get(key) is True for key in keys):
        return
    lines = original.splitlines(keepends=True)
    header = re.compile(r"^\s*\[streaming\]\s*(?:#.*)?$")
    start = next((i + 1 for i, line in enumerate(lines) if header.match(line.rstrip())), None)
    if start is None:
        if "streaming" in config:
            raise ValueError("Expected a [streaming] table in the OXRSys profile.")
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\n[streaming]\n")
        start = len(lines)
    end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    section = "".join(lines[start:end])
    for key in keys:
        section, count = re.subn(rf"(?m)^([ \t]*{key}[ \t]*=[ \t]*)(true|false)\b",
                                 r"\g<1>true", section)
        if count == 0:
            if key in config.get("streaming", {}):
                raise ValueError(f"Unsupported value for streaming.{key} in {path}")
            if section and not section.endswith("\n"):
                section += "\n"
            section += f"{key} = true\n"
    updated = "".join(lines[:start]) + section + "".join(lines[end:])
    if not all(tomllib.loads(updated)["streaming"][key] is True for key in keys):
        raise ValueError("Could not enable OXRSys camera composition.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as temp:
        temp.write(updated)
    pending = Path(temp.name)
    try:
        if (path.read_text() if path.exists() else None) != before:
            raise RuntimeError("OXRSys settings changed during preparation. Retry the launch.")
        if before is not None:
            pending.chmod(path.stat().st_mode & 0o777)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
