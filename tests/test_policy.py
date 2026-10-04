from shadow.policy import chat_is_approved, group_message_needs_reply, split_telegram_message


def test_chat_allowlist() -> None:
    assert chat_is_approved(123, frozenset({123}))
    assert not chat_is_approved(456, frozenset({123}))
    assert chat_is_approved(456, "*")


def test_group_reply_modes() -> None:
    assert group_message_needs_reply(is_private=True, mode="mentions", mentioned=False, replying_to_shadow=False)
    assert not group_message_needs_reply(is_private=False, mode="mentions", mentioned=False, replying_to_shadow=False)
    assert group_message_needs_reply(is_private=False, mode="mentions", mentioned=True, replying_to_shadow=False)
    assert group_message_needs_reply(is_private=False, mode="all", mentioned=False, replying_to_shadow=False)


def test_message_splitting() -> None:
    chunks = split_telegram_message("hello world " * 100, 100)
    assert chunks
    assert all(len(chunk) <= 100 for chunk in chunks)
