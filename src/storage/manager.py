"""Storage manager for configuration and state persistence."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Config, ContentItem

logger = logging.getLogger(__name__)


class StorageManager:
    """Manages file-based storage for configuration and state."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "config.json"
        self.summaries_dir = self.data_dir / "summaries"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> Config:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                f"Please create it based on the template in README.md"
            )

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return Config.model_validate(data)

    def save_daily_summary(self, date: str, markdown: str, language: str = "en") -> Path:
        filename = f"horizon-{date}-{language}.md"
        filepath = self.summaries_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown)

        return filepath

    def load_subscribers(self) -> list:
        """Loads the list of email subscribers."""
        subscribers_path = self.data_dir / "subscribers.json"
        if not subscribers_path.exists():
            return []

        try:
            with open(subscribers_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []

    def add_subscriber(self, email_addr: str):
        """Adds a new subscriber email."""
        subscribers = self.load_subscribers()
        if email_addr not in subscribers:
            subscribers.append(email_addr)
            self._save_subscribers(subscribers)

    def remove_subscriber(self, email_addr: str):
        """Removes a subscriber email."""
        subscribers = self.load_subscribers()
        if email_addr in subscribers:
            subscribers.remove(email_addr)
            self._save_subscribers(subscribers)

    def save_brief(self, date: str, markdown: str) -> Path:
        """Save concise brief Markdown summary."""
        path = self.summaries_dir / f"horizon-{date}-brief.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def save_html(self, date: str, html: str) -> Path:
        """Save detailed HTML report."""
        html_dir = self.data_dir / "html"
        html_dir.mkdir(exist_ok=True)
        path = html_dir / f"horizon-{date}.html"
        path.write_text(html, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Intermediate cache (grouped items after scoring + enrichment)
    # ------------------------------------------------------------------

    def _cache_dir(self) -> Path:
        d = self.data_dir / "cache"
        d.mkdir(exist_ok=True)
        return d

    def save_grouped_items(
        self,
        date: str,
        grouped: Dict[str, List[ContentItem]],
        total_fetched: int,
    ) -> Path:
        """Persist enriched grouped items so later steps can skip fetch/score/enrich."""
        payload = {
            "date": date,
            "total_fetched": total_fetched,
            "groups": {
                name: [item.model_dump(mode="json") for item in items]
                for name, items in grouped.items()
            },
        }
        path = self._cache_dir() / f"{date}-items.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("cache: saved %d groups to %s", len(grouped), path)
        return path

    def load_grouped_items(self, date: str) -> Optional[Dict]:
        """Load cached grouped items. Returns dict with keys 'groups' and 'total_fetched', or None."""
        path = self._cache_dir() / f"{date}-items.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            groups: Dict[str, List[ContentItem]] = {}
            for name, items_raw in raw["groups"].items():
                groups[name] = [ContentItem.model_validate(d) for d in items_raw]
            return {"groups": groups, "total_fetched": raw["total_fetched"]}
        except Exception as e:
            logger.warning("cache: failed to load %s: %s", path, e)
            return None

    def save_podcast(self, date: str, audio_bytes: bytes) -> Path:
        """Save podcast MP3 file, consistent with save_html/save_brief pattern."""
        podcast_dir = self.data_dir / "podcasts"
        podcast_dir.mkdir(exist_ok=True)
        path = podcast_dir / f"horizon-{date}.mp3"
        path.write_bytes(audio_bytes)
        return path

    def _save_subscribers(self, subscribers: list):
        """Helper to save subscribers list."""
        subscribers_path = self.data_dir / "subscribers.json"
        with open(subscribers_path, "w", encoding="utf-8") as f:
            json.dump(subscribers, f, indent=2)
