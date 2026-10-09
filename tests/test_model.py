import unittest

from model.build import (Build, InstalledBuild, fork_label, fork_series,
                         human_size, minor_of, upbge_series, version_tuple)


class ModelTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(version_tuple("5.2.1"), (5, 2, 1))
        self.assertEqual(version_tuple("4.5"), (4, 5))
        self.assertEqual(version_tuple(""), (0,))

    def test_minor_of(self):
        self.assertEqual(minor_of("5.2.1"), "5.2")
        self.assertEqual(minor_of("4.5"), "4.5")
        self.assertEqual(minor_of("2"), "2")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1024), "1.0 KB")
        self.assertEqual(human_size(1024 * 1024 * 350), "350.0 MB")

    def test_upbge_series(self):
        """UPBGE numera a su manera; la serie de Blender es la del punto movido."""
        self.assertEqual(upbge_series("0.53"), "5.3")
        self.assertEqual(upbge_series("0.40"), "4.0")
        self.assertEqual(upbge_series("0.36"), "3.6")
        # Sin la forma esperada se devuelve la serie tal cual, sin inventar.
        self.assertEqual(upbge_series("1.0"), "1.0")

    def test_fork_series(self):
        self.assertEqual(fork_series("upbge", "0.53"), "5.3")
        self.assertEqual(fork_series("bforartists", "5.2.0"), "5.2")
        self.assertEqual(fork_series("", "5.2.0"), "5.2")

    def test_fork_label(self):
        self.assertEqual(fork_label(""), "Blender")
        self.assertEqual(fork_label("bforartists"), "Bforartists")
        self.assertEqual(fork_label("upbge"), "UPBGE")

    def test_fork_name(self):
        build = Build("5.2.0", "bforartists", "stable", "linux", "x86_64",
                      "u", "f.tar.xz", fork="bforartists")
        self.assertEqual(build.fork_name, "Bforartists")
        plain = Build("5.2.0", "v52", "stable", "linux", "x86_64", "u", "f")
        self.assertEqual(plain.fork_name, "Blender")

    def test_is_lts(self):
        stable = Build("4.5.13", "v45", "stable", "linux", "x86_64", "u", "f.tar.xz")
        self.assertTrue(stable.is_lts)
        non_lts = Build("4.4.3", "v44", "stable", "linux", "x86_64", "u", "f.tar.xz")
        self.assertFalse(non_lts.is_lts)
        alpha = Build("5.3.0", "main", "alpha", "linux", "x86_64", "u", "f.tar.xz")
        self.assertFalse(alpha.is_lts)

    def test_installed_build(self):
        entry = InstalledBuild("blender-4.5.13-linux-x64", "/tmp/x", "4.5.13", "/tmp/x/blender")
        self.assertEqual(entry.fork_name, "Blender")
        self.assertEqual(entry.favorite_key, "|4.5.13")


if __name__ == "__main__":
    unittest.main()
