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


def test_group_reply_update_validation() -> None:
    import pytest
    from shadow.policy import parse_group_reply_update

    assert parse_group_reply_update({"enabled": False}) == (False, None)
    assert parse_group_reply_update({"mode": "all"}) == (None, "all")
    assert parse_group_reply_update({"enabled": True, "mode": "mentions"}) == (True, "mentions")
    for bad in ([], {}, {"enabled": "yes"}, {"enabled": 1}, {"mode": "everyone"}, {"mode": None, "enabled": None}):
        with pytest.raises(ValueError):
            parse_group_reply_update(bad)
