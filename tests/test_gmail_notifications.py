from wonjae_dispatcher_runner.gmail_notifications import (
    Notification,
    NotificationService,
    _imap_mailbox_name,
    notification_key,
)


class FakeClient:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.sent = []
        self.labels = []
        self.trashed = []

    def notification_exists(self, key):
        return key in self.existing

    def send(self, notification):
        self.sent.append(notification.key)
        self.existing.add(notification.key)

    def wait_for_notification(self, key, attempts=8, delay=1.5):
        return key in self.existing

    def apply_label(self, key, label):
        self.labels.append((key, label))

    def trash_notification(self, key):
        if key not in self.existing:
            return False
        self.trashed.append(key)
        return True


def test_notification_subject_and_key() -> None:
    note = Notification("WAFL", "WAFL-TEST-001", 2, "FAILED")
    assert note.key == "WAFL|WAFL-TEST-001|2|FAILED"
    assert note.subject == "[WAFL-CODEX][RESULT][FAILED] WAFL-TEST-001"
    assert "Notification-Key: WAFL|WAFL-TEST-001|2|FAILED" in note.body()


def test_started_delivery_is_idempotent() -> None:
    note = Notification("CLASSMO", "CLASSMO-TEST-001", 1, "STARTED")
    client = FakeClient()
    service = NotificationService(client)
    first = service.deliver(note)
    second = service.deliver(note)
    assert first["sent"] is True
    assert second["sent"] is False
    assert client.sent == [note.key]


def test_terminal_trashes_same_attempt_started() -> None:
    started = notification_key("ESC", "ESC-TEST-001", 1, "STARTED")
    client = FakeClient({started})
    note = Notification("ESC", "ESC-TEST-001", 1, "FAILED")
    outcome = NotificationService(client).deliver(note)
    assert outcome["cleaned_keys"] == (started,)
    assert client.trashed == [started]


def test_higher_attempt_started_supersedes_prior_failed_result() -> None:
    prior = notification_key("MUVEL", "MUVEL-TEST-001", 1, "FAILED")
    client = FakeClient({prior})
    note = Notification("MUVEL", "MUVEL-TEST-001", 2, "STARTED")
    outcome = NotificationService(client).deliver(note)
    assert outcome["cleaned_keys"] == (prior,)
    assert client.trashed == [prior]


def test_terminal_without_started_still_sends_result() -> None:
    client = FakeClient()
    note = Notification("WAFL", "WAFL-PRESTART-001", 1, "FAILED")
    outcome = NotificationService(client).deliver(note)
    assert outcome["sent"] is True
    assert client.sent == [note.key]
    assert client.trashed == []


def test_mailbox_parser_reads_gmail_special_use() -> None:
    flags, name = _imap_mailbox_name(
        b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'
    )
    assert "\\All" in flags
    assert name == "[Gmail]/All Mail"
