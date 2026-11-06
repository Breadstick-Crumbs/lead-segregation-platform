"""Read new mail from any IMAP server. Synchronous (imap-tools), so callers run it in a thread."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from imap_tools import AND, MailBox, U


@dataclass(frozen=True)
class FetchedEmail:
    uid: int
    raw: bytes


@dataclass(frozen=True)
class FetchResult:
    uidvalidity: int
    emails: list[FetchedEmail]


@dataclass(frozen=True)
class ImapCredentials:
    host: str
    port: int
    username: str
    password: str
    folder: str


class MailboxReader(Protocol):
    def fetch_new(
        self,
        credentials: ImapCredentials,
        *,
        last_uid: int | None,
        uidvalidity: int | None,
        since: date,
        limit: int,
    ) -> FetchResult: ...


class ImapToolsReader:
    """Polls by UID. If the server's UIDVALIDITY changed (folder rebuilt), old UIDs mean
    nothing, so we fall back to a date window and let Message-ID dedupe absorb the overlap."""

    def fetch_new(
        self,
        credentials: ImapCredentials,
        *,
        last_uid: int | None,
        uidvalidity: int | None,
        since: date,
        limit: int,
    ) -> FetchResult:
        with MailBox(credentials.host, credentials.port, timeout=30).login(
            credentials.username, credentials.password, initial_folder=credentials.folder
        ) as mailbox:
            current = int(mailbox.folder.status(credentials.folder, ["UIDVALIDITY"])["UIDVALIDITY"])
            resume = last_uid is not None and uidvalidity == current
            criteria = (
                AND(uid=U(str(last_uid + 1), "*"))
                if resume and last_uid is not None
                else AND(date_gte=since)
            )
            emails: list[FetchedEmail] = []
            for item in mailbox.fetch(criteria, mark_seen=False, limit=limit, bulk=True):
                uid = int(item.uid or 0)
                # "N:*" always returns the newest message, even when it is older than N.
                if resume and last_uid is not None and uid <= last_uid:
                    continue
                emails.append(FetchedEmail(uid=uid, raw=item.obj.as_bytes()))
        return FetchResult(uidvalidity=current, emails=sorted(emails, key=lambda e: e.uid))
