from __future__ import annotations

import hashlib
import imaplib
import re
import smtplib
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Callable, Iterator

HEADER_KEY = "X-WONJAE-Notification-Key"
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "MANUAL_REQUIRED", "CANCELLED"})
PRIOR_RETRY_TERMINALS = ("FAILED", "MANUAL_REQUIRED", "CANCELLED")
ALLOWED_STATUSES = frozenset({"STARTED", *TERMINAL_STATUSES})


class GmailNotificationError(RuntimeError):
    pass


def notification_key(project: str, task_id: str, attempt: int, status: str) -> str:
    return f"{project}|{task_id}|{attempt}|{status}"


@dataclass(frozen=True)
class Notification:
    project: str
    task_id: str
    attempt: int
    status: str
    profile: str = ""
    control_sha: str = ""
    source_sha: str = ""
    product_pr: str = ""
    exact_head_sha: str = ""
    integration_evidence: str = ""
    smallest_next_action: str = ""

    def __post_init__(self) -> None:
        if not self.project or not self.task_id:
            raise ValueError("project and task_id are required")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported notification status: {self.status}")

    @property
    def key(self) -> str:
        return notification_key(self.project, self.task_id, self.attempt, self.status)

    @property
    def subject(self) -> str:
        if self.status == "STARTED":
            return f"[{self.project}-CODEX][STARTED] {self.task_id}"
        return f"[{self.project}-CODEX][RESULT][{self.status}] {self.task_id}"

    @property
    def label(self) -> str:
        return f"{self.project}-CODEX"

    def body(self) -> str:
        heading = (
            f"STARTED — {self.task_id}"
            if self.status == "STARTED"
            else f"{self.status} — {self.task_id}"
        )
        rows = [
            heading,
            "",
            f"Project: {self.project}",
            f"Task ID: {self.task_id}",
            f"Attempt: {self.attempt}",
            f"Status: {self.status}",
        ]
        optional = (
            ("Profile", self.profile),
            ("Control SHA", self.control_sha),
            ("Source SHA", self.source_sha),
            ("Product PR", self.product_pr),
            ("Exact head SHA", self.exact_head_sha),
            ("Integration evidence", self.integration_evidence),
            ("Smallest next action", self.smallest_next_action),
        )
        rows.extend(f"{name}: {value}" for name, value in optional if value)
        rows.extend(["", f"Notification-Key: {self.key}"])
        return "\n".join(rows)


def _imap_mailbox_name(line: bytes) -> tuple[set[str], str] | None:
    text = line.decode("utf-8", errors="replace")
    match = re.match(r"^\((?P<flags>[^)]*)\)\s+\S+\s+(?P<name>.+)$", text)
    if not match:
        return None
    flags = set(match.group("flags").split())
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return flags, name


def _quote_mailbox(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', r'\"') + '"'


class GmailClient:
    def __init__(
        self,
        username: str,
        app_password: str,
        *,
        smtp_factory: Callable[..., smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
        imap_factory: Callable[..., imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not username or not app_password:
            raise GmailNotificationError("Gmail username/app password is missing")
        self.username = username
        self.app_password = app_password
        self.smtp_factory = smtp_factory
        self.imap_factory = imap_factory
        self.sleep = sleep

    @contextmanager
    def _imap(self) -> Iterator[imaplib.IMAP4_SSL]:
        client = self.imap_factory("imap.gmail.com", 993, ssl_context=ssl.create_default_context())
        try:
            client.login(self.username, self.app_password)
            yield client
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _mailboxes(self, client: imaplib.IMAP4_SSL) -> dict[str, str]:
        result: dict[str, str] = {"inbox": "INBOX"}
        status, lines = client.list()
        if status != "OK" or not lines:
            return result
        for raw in lines:
            if not isinstance(raw, bytes):
                continue
            parsed = _imap_mailbox_name(raw)
            if not parsed:
                continue
            flags, name = parsed
            if "\\All" in flags:
                result["all"] = name
            if "\\Trash" in flags:
                result["trash"] = name
        result.setdefault("all", "[Gmail]/All Mail")
        result.setdefault("trash", "[Gmail]/Trash")
        return result

    @staticmethod
    def _search_selected(client: imaplib.IMAP4_SSL, key: str) -> tuple[str, ...]:
        status, payload = client.uid("SEARCH", None, "HEADER", HEADER_KEY, f'"{key}"')
        if status != "OK" or not payload:
            return ()
        raw = payload[0] or b""
        if isinstance(raw, str):
            raw = raw.encode()
        candidates = tuple(part.decode("ascii") for part in raw.split() if part)
        exact = []
        for uid in candidates:
            status, entries = client.uid("FETCH", uid, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_KEY})])")
            if status != "OK":
                raise GmailNotificationError("notification header readback failed")
            headers = [entry[1] for entry in entries if isinstance(entry, tuple)]
            if len(headers) != 1 or not isinstance(headers[0], bytes):
                raise GmailNotificationError("notification header readback was ambiguous")
            message = BytesParser(policy=policy.default).parsebytes(headers[0], headersonly=True)
            if message.get_all(HEADER_KEY) == [key]:
                exact.append(uid)
        return tuple(exact)

    def _find_in(self, client: imaplib.IMAP4_SSL, mailbox: str, key: str) -> tuple[str, ...]:
        status, _ = client.select(_quote_mailbox(mailbox))
        if status != "OK":
            return ()
        return self._search_selected(client, key)

    def notification_exists(self, key: str) -> bool:
        with self._imap() as client:
            boxes = self._mailboxes(client)
            return bool(
                self._find_in(client, boxes["all"], key)
                or self._find_in(client, boxes["trash"], key)
            )

    def wait_for_notification(self, key: str, attempts: int = 8, delay: float = 1.5) -> bool:
        for index in range(attempts):
            if self.notification_exists(key):
                return True
            if index + 1 < attempts:
                self.sleep(delay)
        return False

    def send(self, notification: Notification) -> None:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = self.username
        message["Subject"] = notification.subject
        message[HEADER_KEY] = notification.key
        message["X-WONJAE-Project"] = notification.project
        message["X-WONJAE-Task-ID"] = notification.task_id
        message["X-WONJAE-Attempt"] = str(notification.attempt)
        message["X-WONJAE-Status"] = notification.status
        digest = hashlib.sha256(
            f"{self.username}|{notification.key}".encode("utf-8")
        ).hexdigest()[:32]
        message["Message-ID"] = f"<wonjae-{digest}@github-actions.local>"
        message.set_content(notification.body())

        with self.smtp_factory(
            "smtp.gmail.com",
            465,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(self.username, self.app_password)
            smtp.send_message(message)

    def apply_label(self, key: str, label: str) -> None:
        with self._imap() as client:
            boxes = self._mailboxes(client)
            uids = self._find_in(client, boxes["all"], key)
            if not uids:
                return
            for uid in uids:
                status, _ = client.uid("STORE", uid, "+X-GM-LABELS", f'("{label}")')
                if status != "OK":
                    raise GmailNotificationError(f"failed to apply Gmail label for {key}")

    def trash_notification(self, key: str) -> bool:
        with self._imap() as client:
            boxes = self._mailboxes(client)
            if (self._find_in(client, boxes["trash"], key)
                    and not self._find_in(client, boxes["inbox"], key)):
                return True

            source = boxes["all"]
            uids = self._find_in(client, source, key)
            if not uids:
                uids = self._find_in(client, boxes["inbox"], key)
                source = boxes["inbox"]
            if not uids:
                return False

            for uid in uids:
                status, _ = client.uid("MOVE", uid, _quote_mailbox(boxes["trash"]))
                if status != "OK":
                    status, _ = client.uid("COPY", uid, _quote_mailbox(boxes["trash"]))
                    if status != "OK":
                        raise GmailNotificationError(f"failed to copy {key} to Trash")
                    status, _ = client.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
                    if status != "OK":
                        raise GmailNotificationError(f"failed to remove source message for {key}")
                    status, _ = client.uid("EXPUNGE", uid)
                    if status != "OK":
                        raise GmailNotificationError(f"failed to expunge exact message for {key}")

            trash_found = bool(self._find_in(client, boxes["trash"], key))
            inbox_found = bool(self._find_in(client, boxes["inbox"], key))
            if not trash_found or inbox_found:
                raise GmailNotificationError(
                    f"Trash verification failed for {key}: "
                    f"trash={trash_found} inbox={inbox_found}"
                )
            return True


class NotificationService:
    def __init__(self, client: GmailClient) -> None:
        self.client = client

    def deliver(self, notification: Notification) -> dict[str, object]:
        existed = self.client.notification_exists(notification.key)
        if not existed:
            self.client.send(notification)
            if not self.client.wait_for_notification(notification.key):
                raise GmailNotificationError(
                    f"sent notification was not readable by exact key: {notification.key}"
                )
        self.client.apply_label(notification.key, notification.label)

        cleaned: list[str] = []
        if notification.status == "STARTED" and notification.attempt > 1:
            previous_attempt = notification.attempt - 1
            for status in PRIOR_RETRY_TERMINALS:
                key = notification_key(
                    notification.project,
                    notification.task_id,
                    previous_attempt,
                    status,
                )
                if self.client.notification_exists(key):
                    if self.client.trash_notification(key):
                        cleaned.append(key)
                    break
        elif notification.status in TERMINAL_STATUSES:
            started_key = notification_key(
                notification.project,
                notification.task_id,
                notification.attempt,
                "STARTED",
            )
            if self.client.notification_exists(started_key):
                if self.client.trash_notification(started_key):
                    cleaned.append(started_key)

        return {
            "notification_key": notification.key,
            "sent": not existed,
            "cleaned_keys": tuple(cleaned),
        }
