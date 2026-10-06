import asyncio
import unittest

from shadow.presence import INTERVAL_SECONDS, OnlinePresence


class FakeClient:
    def __init__(self, fail=False, connected=True):
        self.calls = []
        self.fail = fail
        self.connected = connected

    def is_connected(self):
        return self.connected

    async def __call__(self, request):
        if self.fail:
            raise ConnectionError("down")
        self.calls.append(request)


class PresenceTests(unittest.TestCase):
    def test_ping_marks_account_online(self):
        client = FakeClient()
        presence = OnlinePresence(True)
        self.assertTrue(asyncio.run(presence.ping(client)))
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.calls[0].offline)
        self.assertEqual(presence.ok_count, 1)
        self.assertIsNone(presence.status()["last_error"])

    def test_failure_is_counted_and_never_raises(self):
        presence = OnlinePresence(True)
        self.assertFalse(asyncio.run(presence.ping(FakeClient(fail=True))))
        self.assertEqual((presence.fail_count, presence.last_error), (1, "ConnectionError"))

    def test_loop_pings_only_a_connected_client(self):
        async def scenario(client):
            presence = OnlinePresence(True, interval=0.01)
            presence.start(lambda: client)
            await asyncio.sleep(0.1)
            presence.stop()
            return presence

        online = FakeClient()
        asyncio.run(scenario(online))
        self.assertGreaterEqual(len(online.calls), 2)
        offline = FakeClient(connected=False)
        asyncio.run(scenario(offline))
        self.assertEqual(offline.calls, [])
        asyncio.run(scenario(None))

    def test_disabled_never_starts(self):
        async def scenario():
            presence = OnlinePresence(False, interval=0.01)
            client = FakeClient()
            presence.start(lambda: client)
            await asyncio.sleep(0.05)
            return client, presence

        client, presence = asyncio.run(scenario())
        self.assertEqual(client.calls, [])
        self.assertIsNone(presence._task)

    def test_interval_is_below_telegrams_online_window(self):
        self.assertLessEqual(INTERVAL_SECONDS, 180)
        self.assertTrue(all(0.9 * 120 <= OnlinePresence(True).next_delay() <= 1.1 * 120 for _ in range(100)))


if __name__ == "__main__":
    unittest.main()
