import importlib.util
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile


spec = importlib.util.spec_from_file_location("sync_release", Path(__file__).with_name("sync-release.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class ReleaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.manifest = self.base / "manifest"
        self.original = {
            "_worker.js": b"original worker", "VERSION.txt": b"v1\n",
            "wrangler.jsonc": b"local config", "package.json": b"local dependencies",
            "package-lock.json": b"local lock", ".gitignore": b"local ignores",
            ".github/workflows/sync-from-cfnew.yml": b"local workflow",
        }
        for name, data in self.original.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def apply(self, entries):
        archive = self.base / "release.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive, "w") as output:
                for name, data in entries:
                    output.writestr(name, data)
        sync.sync_release(archive, "v2", self.root, self.manifest)

    def assert_original(self):
        for name, data in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse(self.manifest.exists())

    def test_complete_release_includes_new_assets_and_modules(self):
        entries = [("_worker.js", b"new worker"), ("assets/index.html", b"html"),
                   ("lib/helper.js", b"helper"), ("README.md", b"upstream docs")]
        self.apply(entries)
        for name, data in entries:
            self.assertEqual((self.root / name).read_bytes(), data)
        for name, data in self.original.items():
            if name not in {"_worker.js", "VERSION.txt"}:
                self.assertEqual((self.root / name).read_bytes(), data)
        expected = {name.encode() for name, _ in entries} | {b"VERSION.txt"}
        self.assertEqual(set(self.manifest.read_bytes().split(b"\0")[:-1]), expected)
        self.assertEqual((self.root / "VERSION.txt").read_bytes(), b"v2\n")

    def test_identical_protected_file_is_accepted(self):
        self.apply([("_worker.js", b"new"), ("wrangler.jsonc", b"local config")])
        self.assertEqual((self.root / "wrangler.jsonc").read_bytes(), b"local config")

    def test_configuration_conflicts_stop_before_any_changes(self):
        for name in ["wrangler.jsonc", "wrangler.toml", "package.json", "package-lock.json",
                     ".gitignore", ".github/workflows/sync-from-cfnew.yml"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "configuration conflict"):
                    self.apply([("_worker.js", b"new"), (name, b"upstream configuration")])
                self.assert_original()

    def test_missing_or_empty_entry_requires_compatibility_review(self):
        for entries in [[("src/worker.js", b"new")], [("_worker.js", b"")]]:
            with self.assertRaisesRegex(ValueError, "entry point"):
                self.apply(entries)
            self.assert_original()

    def test_duplicate_and_traversal_entries_are_rejected(self):
        for entries in [[("_worker.js", b"a"), ("_worker.js", b"b")],
                        [("_worker.js", b"new"), ("../outside", b"escape")]]:
            with self.assertRaises(ValueError):
                self.apply(entries)
            self.assert_original()
        self.assertFalse((self.base / "outside").exists())

    def test_archive_symlink_is_rejected(self):
        link = zipfile.ZipInfo("assets/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with self.assertRaisesRegex(ValueError, "Non-regular"):
            self.apply([("_worker.js", b"new"), (link, b"../../outside")])
        self.assert_original()

    def test_file_directory_collision_stops_before_writes(self):
        with self.assertRaisesRegex(ValueError, "collision"):
            self.apply([("_worker.js", b"new"), ("assets", b"file"), ("assets/a.js", b"a")])
        self.assert_original()


if __name__ == "__main__":
    unittest.main()
