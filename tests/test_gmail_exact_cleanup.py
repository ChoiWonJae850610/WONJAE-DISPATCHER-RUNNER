from contextlib import contextmanager

import pytest

from wonjae_dispatcher_runner.gmail_notifications import (
    HEADER_KEY,
    GmailClient,
    GmailNotificationError,
    Notification,
    NotificationService,
)

KEY = "ESC|ESC-TEST-001|2|STARTED"


class IMAP:
    def __init__(self):
        self.box = ""
        self.boxes = {"all": {"1"}, "inbox": {"1"}, "trash": {"2"}}
        self.keys = {"1": KEY, "2": KEY}
        self.moves = []

    def select(self, box):
        self.box = box.strip('"')
        return "OK", []

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(sorted(self.boxes[self.box])).encode()]
        if command == "FETCH":
            return "OK", [(b"header", f"{HEADER_KEY}: {self.keys[args[0]]}\r\n\r\n".encode())]
        if command == "MOVE":
            uid = args[0]
            self.moves.append(uid)
            for box in ("all", "inbox"):
                self.boxes[box].discard(uid)
            self.boxes["trash"].add(uid)
            return "OK", []
        raise AssertionError(command)


def test_existing_trash_copy_does_not_hide_started_inbox_residue(monkeypatch):
    imap = IMAP()
    client = GmailClient("synthetic", "synthetic")

    @contextmanager
    def connect():
        yield imap

    monkeypatch.setattr(client, "_imap", connect)
    monkeypatch.setattr(client, "_mailboxes", lambda _: {k: k for k in imap.boxes})
    assert client.trash_notification(KEY)
    assert imap.moves == ["1"]
    assert imap.boxes["inbox"] == set()
    assert client.trash_notification(KEY)
    assert imap.moves == ["1"]


def test_imap_header_search_is_exact_not_substring():
    imap = IMAP()
    imap.box = "inbox"
    imap.boxes["inbox"].add("3")
    imap.keys["3"] = KEY + "-unrelated"
    assert GmailClient._search_selected(imap, KEY) == ("1",)


def test_result_readback_failure_never_trashes_started():
    class Client:
        def notification_exists(self, key):
            return False

        def send(self, note):
            pass

        def wait_for_notification(self, key):
            return False

        def trash_notification(self, key):
            raise AssertionError("STARTED must remain before RESULT readback")

    with pytest.raises(GmailNotificationError, match="not readable"):
        NotificationService(Client()).deliver(Notification("ESC", "ESC-TEST-001", 2, "COMPLETED"))
