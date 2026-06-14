import json
import os
import sys
import tempfile
import types
import unittest

sys.modules.setdefault("requests", types.SimpleNamespace())
sys.modules.setdefault("myx_classes", types.SimpleNamespace())
sys.modules.setdefault("myx_utilities", types.SimpleNamespace())

import myx_mam


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, path=None, default=None):
        value = self.data
        for part in path.split("/"):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


class MamSessionTests(unittest.TestCase):
    def cfg(self, **values):
        config = {
            "session": "static-token",
            "mousehole_enabled": 0,
            "mousehole_state_file": "",
        }
        config.update(values)
        return FakeConfig({"Config": config})

    def write_state(self, state):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        with tmp:
            json.dump(state, tmp)
        return tmp.name

    def test_static_session_is_used_when_mousehole_is_disabled(self):
        cfg = self.cfg(session="static-token", mousehole_enabled=0)

        self.assertEqual(myx_mam.getMAMSession(cfg), "static-token")

    def test_mousehole_session_is_read_and_decoded(self):
        state_file = self.write_state({"currentCookie": "token%2Bwith%2Fencoding"})
        cfg = self.cfg(mousehole_enabled=1, mousehole_state_file=state_file)

        self.assertEqual(myx_mam.getMAMSession(cfg), "token+with/encoding")

    def test_missing_mousehole_state_falls_back_to_static_session(self):
        cfg = self.cfg(
            session="fallback-token",
            mousehole_enabled=1,
            mousehole_state_file="/path/does/not/exist",
        )

        self.assertEqual(myx_mam.getMAMSession(cfg), "fallback-token")

    def test_invalid_mousehole_state_falls_back_to_static_session(self):
        state_file = self.write_state({"unexpected": "value"})
        cfg = self.cfg(
            session="fallback-token",
            mousehole_enabled=1,
            mousehole_state_file=state_file,
        )

        self.assertEqual(myx_mam.getMAMSession(cfg), "fallback-token")

    def test_empty_static_session_remains_empty_when_mousehole_fallback_fails(self):
        cfg = self.cfg(
            session="",
            mousehole_enabled=1,
            mousehole_state_file="/path/does/not/exist",
        )

        self.assertEqual(myx_mam.getMAMSession(cfg), "")


if __name__ == "__main__":
    unittest.main()
