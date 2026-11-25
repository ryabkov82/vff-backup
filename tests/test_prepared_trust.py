"""Exercise the actual path-trust checker, including pre-install checks."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ansible/roles/backup/files"))
from prepared_trust import main, trusted_path


class PreparedTrustTests(unittest.TestCase):
    def test_trusted_file_and_safe_future_install(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("VFF_TEST_TMPDIR", str(Path.home()))) as tmp:
            path = Path(tmp)
            leaf = path / "file"
            leaf.write_text("fixture")
            trusted_path(str(leaf))
            trusted_path(str(path / "future" / "file"), allow_missing=True)
            with self.assertRaises(FileNotFoundError):
                trusted_path(str(path / "missing"))
            main([str(leaf)])
            main(["--allow-missing", str(path / "future")])
            with self.assertRaises(ValueError):
                main([])

    def test_writable_ancestor_and_future_leaf(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("VFF_TEST_TMPDIR", str(Path.home()))) as tmp:
            parent = Path(tmp) / "unsafe"
            parent.mkdir()
            parent.chmod(0o775)
            leaf = parent / "file"
            leaf.write_text("fixture")
            leaf.chmod(0o600)
            for path in (leaf, parent / "future"):
                with self.assertRaises(ValueError):
                    trusted_path(str(path), allow_missing=True)

    def test_public_sticky_ancestor_is_not_trusted(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("VFF_TEST_TMPDIR", str(Path.home()))) as tmp:
            public = Path(tmp) / "public"
            public.mkdir()
            public.chmod(0o1777)
            leaf = public / "private"
            leaf.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                trusted_path(str(leaf))
            with self.assertRaises(ValueError):
                trusted_path(str(public / "missing"), allow_missing=True)

    def test_symlink_noncanonical_and_hardlink(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("VFF_TEST_TMPDIR", str(Path.home()))) as tmp:
            root = Path(tmp)
            leaf = root / "file"
            leaf.write_text("fixture")
            alias = root / "alias"
            alias.symlink_to(leaf)
            for path in (str(alias), str(root) + "/../file", "relative", str(root) + "//file"):
                with self.assertRaises(ValueError):
                    trusted_path(path)
            os.link(leaf, root / "hard")
            with self.assertRaises(ValueError):
                trusted_path(str(leaf))
