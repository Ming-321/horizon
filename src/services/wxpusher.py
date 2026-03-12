"""WxPusher notification service for pushing messages to WeChat."""

import logging
import os
from typing import List

from ..models import WxPusherConfig

logger = logging.getLogger(__name__)

MAX_SUMMARY_LEN = 20
MAX_CONTENT_LEN = 40000


class WxPusherService:
    """Wraps the WxPusher SDK to push HTML messages."""

    def __init__(self, config: WxPusherConfig):
        self.app_token = os.getenv(config.app_token_env, "")
        self.uids: List[str] = [
            u.strip() for u in os.getenv(config.uids_env, "").split(",") if u.strip()
        ]
        self.topic_ids: List[int] = []
        for t in os.getenv(config.topic_ids_env, "").split(","):
            t = t.strip()
            if t:
                try:
                    self.topic_ids.append(int(t))
                except ValueError:
                    logger.warning("invalid topic_id '%s', skipped", t)

    def push(self, content_html: str, summary: str) -> bool:
        """Push HTML message to WeChat via WxPusher.

        Returns True if at least one recipient succeeded.
        Truncates summary to 20 chars and content to 40000 chars.
        Logs warnings on failure; never raises.
        """
        if not self.app_token:
            logger.warning("wxpusher: app_token is empty, skipping push")
            return False
        if not self.uids and not self.topic_ids:
            logger.warning("wxpusher: no uids or topic_ids configured, skipping push")
            return False

        summary = summary[:MAX_SUMMARY_LEN]
        if len(content_html) > MAX_CONTENT_LEN:
            truncated = content_html[:MAX_CONTENT_LEN]
            last_close = truncated.rfind("</")
            if last_close > MAX_CONTENT_LEN // 2:
                end = truncated.find(">", last_close)
                if end != -1:
                    truncated = truncated[: end + 1]
            content_html = truncated

        try:
            from wxpusher import WxPusher

            result = WxPusher.send_message(
                content_html,
                token=self.app_token,
                uids=self.uids,
                topic_ids=self.topic_ids,
                content_type=2,
                summary=summary,
            )
            if not isinstance(result, dict):
                logger.warning("wxpusher: unexpected result type: %s", type(result))
                return False
            if not result.get("success", False):
                logger.warning("wxpusher: API error: %s", result.get("msg"))
                return False
            data = result.get("data", [])
            any_delivered = any(
                isinstance(d, dict) and d.get("code") == 1000
                for d in data
            )
            if any_delivered:
                logger.info("wxpusher: push succeeded")
            else:
                statuses = [d.get("status", "unknown") for d in data if isinstance(d, dict)]
                logger.warning("wxpusher: no recipient delivered: %s", statuses)
            return any_delivered
        except Exception as e:
            logger.warning("wxpusher: push failed: %s", e)
            return False
