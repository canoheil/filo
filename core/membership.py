"""Validated Telegram channel configuration (no network or credentials)."""
from dataclasses import dataclass
import re
from urllib.parse import urlparse


@dataclass(frozen=True)
class RequiredChannel:
    chat_id: str | int
    join_url: str


@dataclass(frozen=True)
class ChannelPolicy:
    revision: int
    channels: tuple[RequiredChannel, ...]


def telegram_url(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.netloc != "t.me" or parsed.query or parsed.fragment
            or not re.fullmatch(r"/(?:[A-Za-z][A-Za-z0-9_]{4,31}|\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)/?", parsed.path)):
        raise ValueError("Use an HTTPS t.me channel or invite URL")
    return value.rstrip("/")


def required_channels(channel: str, join_url: str = "") -> tuple[RequiredChannel, ...]:
    channel, join_url = channel.strip(), join_url.strip()
    if not channel:
        if join_url:
            raise ValueError("SUPPORT_CHANNEL is required when a join URL is set")
        return ()
    if channel.startswith("https://"):
        channel_url = telegram_url(channel)
        name = urlparse(channel_url).path.strip("/")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name):
            raise ValueError("Invite URLs cannot identify a channel; use its -100 ID and a separate join URL")
        channel = "@" + name
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", channel):
        return (RequiredChannel(channel, telegram_url(join_url) if join_url else f"https://t.me/{channel[1:]}"),)
    if re.fullmatch(r"-100[0-9]+", channel) and len(channel) <= 20 and -(2**63) <= int(channel) < 0:
        if not join_url:
            raise ValueError("Private channel IDs require SUPPORT_CHANNEL_JOIN_URL")
        return (RequiredChannel(int(channel), telegram_url(join_url)),)
    raise ValueError("SUPPORT_CHANNEL must be @username, https://t.me/username, or a -100 channel ID")
