import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "shadow" / "dashboard.html").read_text(encoding="utf-8")


class ChatTabFilterTests(unittest.TestCase):
    def test_each_tab_shows_only_its_own_kind(self):
        start = HTML.index("function filterChats()")
        body = HTML[start:HTML.index("}", HTML.index("noMatches.hidden", start))]
        self.assertIn("kindByView={chats:'Shaxsiy chat',groups:'Guruh',channels:'Kanal',bots:'Bot'}", body)
        self.assertIn("row.dataset.kind===kindByView[currentView]", body)

    def test_list_labels_follow_the_tab(self):
        for text in ("Shaxsiy suhbatlar", "Barcha guruhlar", "Guruh nomini qidirish", "Kanal nomini qidirish", "Bot nomini qidirish",
                     "applyListLabels();$('.top h1')"):
            self.assertIn(text, HTML)

    def test_kinds_used_by_the_filter_match_what_the_server_sends(self):
        agent = (Path(__file__).resolve().parents[1] / "shadow" / "telegram_agent.py").read_text(encoding="utf-8")
        for kind in ("Kanal", "Guruh", "Shaxsiy chat", "Bot"):
            self.assertIn(f'"{kind}"', agent)


if __name__ == "__main__":
    unittest.main()
