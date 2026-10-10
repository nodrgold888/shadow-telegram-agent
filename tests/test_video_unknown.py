import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import mock



class FakeClient:
    def __init__(self):
        self.send_file = None

    async def send_message(self, chat_id, text):
        pass

    async def send_read_acknowledge(self, chat_id):
        pass

    def action(self, chat_id, kind):
        class Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *args):
                return False
        return Ctx()

REEL = "https://www.instagram.com/reel/DeHZHKVK3ki/"


def make_agent(**env):
    from shadow.config import Settings
    from shadow.telegram_agent import TelegramAgent
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "APPROVED_CHAT_IDS": "5", **env}):
        agent = TelegramAgent(Settings.from_env())
    agent.connected = True
    agent.reply_enabled = True
    agent.assistant = SimpleNamespace(last_model=None, last_provider=None)
    agent.client = FakeClient()
    agent.client.send_file = mock.AsyncMock()
    agent._me_id = 111
    agent.video_unknown = True
    return agent


def event(agent, chat_id=9, text=REEL, private=True, bot=False):
    async def get_sender():
        return SimpleNamespace(id=chat_id, bot=bot, deleted=False)
    return SimpleNamespace(client=agent.client, chat_id=chat_id, raw_text=text, is_private=private, id=7,
                           message=SimpleNamespace(voice=None, media=None), get_sender=get_sender,
                           reply=mock.AsyncMock())


@asynccontextmanager
async def fake_video(url):
    yield "/tmp/video.mp4"


class UnknownVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_private_chat_gets_the_video_when_switch_is_on(self):
        agent = make_agent()
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_message(event(agent))
        agent.client.send_file.assert_awaited_once()
        self.assertEqual(agent.client.send_file.await_args.args[0], 9)

    async def test_switch_off_means_no_video(self):
        agent = make_agent()
        agent.video_unknown = False
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_message(event(agent))
        agent.client.send_file.assert_not_called()

    async def test_never_for_groups_bots_or_friends(self):
        agent = make_agent()
        agent.friend_ids = {9: "ish"}
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_message(event(agent, chat_id=9))
            await agent._on_message(event(agent, chat_id=10, private=False))
            await agent._on_message(event(agent, chat_id=11, bot=True))
        agent.client.send_file.assert_not_called()

    async def test_video_works_with_auto_replies_off(self):
        agent = make_agent()
        agent.reply_enabled = False
        agent.assistant = None
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_message(event(agent, chat_id=12))  # the reply listener ignores it while replies are off
            agent.client.send_file.assert_not_called()
            await agent._on_video_link(event(agent, chat_id=12))
        agent.client.send_file.assert_awaited_once()
        self.assertEqual(agent.client.send_file.await_args.args[0], 12)

    async def test_video_link_listener_keeps_the_same_limits_with_replies_off(self):
        agent = make_agent()
        agent.reply_enabled = False
        agent.friend_ids = {9: "ish"}
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_video_link(event(agent, chat_id=9))                    # friend
            await agent._on_video_link(event(agent, chat_id=10, private=False))    # stranger group
            await agent._on_video_link(event(agent, chat_id=11, bot=True))         # bot
            agent.video_unknown = False
            await agent._on_video_link(event(agent, chat_id=13))                   # switch off
            await agent._on_video_link(event(agent, chat_id=14, text="salom"))     # not a link
        agent.client.send_file.assert_not_called()

    async def test_approved_chat_gets_the_video_with_replies_off(self):
        agent = make_agent()
        agent.reply_enabled = False
        agent.video_unknown = False
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_video_link(event(agent, chat_id=5))
        agent.client.send_file.assert_awaited_once()

    async def test_the_link_listener_stays_quiet_while_replies_are_on(self):
        agent = make_agent()
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_video_link(event(agent, chat_id=12))
        agent.client.send_file.assert_not_called()  # _on_message handles it then, so it is never sent twice

    async def test_plain_text_is_not_a_video_request(self):
        agent = make_agent()
        agent._maybe_greet_unknown = mock.AsyncMock()
        with mock.patch("shadow.telegram_agent.downloaded_video", fake_video):
            await agent._on_message(event(agent, text="Assalomu alaykum"))
        agent.client.send_file.assert_not_called()
        agent._maybe_greet_unknown.assert_awaited_once()

    async def test_download_error_is_reported_to_the_chat(self):
        from shadow.video_download import VideoDownloadError

        @asynccontextmanager
        async def broken(url):
            raise VideoDownloadError("Video yuklab bo‘lmadi")
            yield

        agent = make_agent()
        ev = event(agent)
        with mock.patch("shadow.telegram_agent.downloaded_video", broken):
            await agent._on_message(ev)
        ev.reply.assert_awaited_once_with("Video yuklab bo‘lmadi")

    async def test_switch_is_saved_and_reported(self):
        agent = make_agent()
        with mock.patch("shadow.telegram_agent.save_video_unknown", new=mock.AsyncMock(return_value=True)) as save:
            result = await agent.set_video_unknown(False)
        save.assert_awaited_once_with(False)
        self.assertEqual(result, {"video_unknown": False, "persisted": True})
        self.assertFalse(agent.status()["video_unknown"]["enabled"])


if __name__ == "__main__":
    unittest.main()
