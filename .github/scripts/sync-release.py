"""Apply a complete upstream release without silently replacing deployment setup."""

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


PROTECTED = {
    "wrangler.jsonc", "wrangler.json", "wrangler.toml",
    "package.json", "package-lock.json", ".gitignore",
}


def sync_release(archive, version, root, manifest):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise ValueError("Invalid release version")
    files = {}
    executable = set()
    conflicts = []
    with zipfile.ZipFile(archive) as release:
        for entry in release.infolist():
            name = entry.filename.rstrip("/")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts
                    or "\\" in name or any(ord(c) < 32 for c in name)
                    or str(path) != name):
                raise ValueError(f"Unsafe archive path: {name!r}")
            if (path.parts[0] in {".git", ".wrangler", "node_modules"}
                    or path.name == ".env" or path.name.startswith(".env.")
                    or path.name == ".dev.vars" or path.name.startswith(".dev.vars.")):
                raise ValueError(f"Local-only archive path requires review: {name}")
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f"Non-regular archive entry: {name}")
            target = root / name
            for ancestor in (target, *target.parents):
                if ancestor == root:
                    break
                if ancestor.is_symlink():
                    raise ValueError(f"Refusing existing symlink: {name}")
            if entry.is_dir():
                if target.exists() and not target.is_dir():
                    raise ValueError(f"Directory conflicts with existing file: {name}")
                continue
            if name in files:
                raise ValueError(f"Duplicate archive entry: {name}")
            if target.exists() and not target.is_file():
                raise ValueError(f"File conflicts with existing directory: {name}")
            payload = release.read(entry)  # Check CRC before changing any repository files.
            files[name] = payload
            if mode & 0o111:
                executable.add(name)
            if name in PROTECTED or path.parts[0] == ".github":
                if not target.is_file() or target.read_bytes() != payload:
                    conflicts.append(name)

    if conflicts:
        raise ValueError("Deployment/sync configuration conflict; check compatibility before syncing: "
                         + ", ".join(sorted(conflicts)))
    if not files.get("_worker.js"):
        raise ValueError("Missing or empty _worker.js; check the upstream entry point before syncing")
    for name in files:
        for parent in PurePosixPath(name).parents:
            if str(parent) in files or (root / parent).is_file():
                raise ValueError(f"Archive file/directory collision: {name}")
    version_path = root / "VERSION.txt"
    if version_path.is_symlink() or (version_path.exists() and not version_path.is_file()):
        raise ValueError("VERSION.txt must be a regular file")

    # Include every upstream file, including newly added assets and modules.
    # VERSION.txt remains the local record of the upstream release tag.
    files["VERSION.txt"] = (version + "\n").encode()
    for name, payload in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        if name in executable and name not in PROTECTED and not name.startswith(".github/"):
            target.chmod(0o755)
    manifest.write_bytes(b"\0".join(os.fsencode(name) for name in sorted(files)) + b"\0")
    print(f"Synced {len(files)} files; deployment and sync configuration preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("version")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        sync_release(args.archive, args.version, Path.cwd(), args.manifest)
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(f"Sync stopped: {error}", file=sys.stderr)
        sys.exit(1)
