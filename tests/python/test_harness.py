import os
import unittest

import context  # noqa: F401


class TestHarness(unittest.TestCase):
    def test_daemon_dir_exists(self):
        self.assertTrue(os.path.isdir(context.DAEMON), context.DAEMON)

    def test_fixture_dir_exists(self):
        self.assertTrue(os.path.isdir(context.FIXTURES), context.FIXTURES)


if __name__ == '__main__':
    unittest.main()
