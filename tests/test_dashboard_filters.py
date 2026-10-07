import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "shadow" / "dashboard.html").read_text(encoding="utf-8")


class ChatTabFilterTests(unittest.TestCase):
    def test_chats_tab_shows_only_private_chats_and_groups_tab_groups_and_channels(self):
        start = HTML.index("function filterChats()")
        body = HTML[start:HTML.index("}", HTML.index("noMatches.hidden", start))]
        self.assertIn("currentView==='groups'?(row.dataset.kind==='Guruh'||row.dataset.kind==='Kanal')", body)
        self.assertIn("currentView==='chats'?row.dataset.kind==='Shaxsiy chat'", body)

    def test_kinds_used_by_the_filter_match_what_the_server_sends(self):
        agent = (Path(__file__).resolve().parents[1] / "shadow" / "telegram_agent.py").read_text(encoding="utf-8")
        for kind in ("Kanal", "Guruh", "Shaxsiy chat"):
            self.assertIn(f'"{kind}"', agent)


if __name__ == "__main__":
    unittest.main()
