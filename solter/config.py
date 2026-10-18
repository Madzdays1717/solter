"""Runtime configuration."""

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    database_path: Path

    @classmethod
    def load(cls) -> 'Config':
        load_dotenv(ROOT / '.env')
        # Reuse the existing database when upgrading an established installation.
        data = ROOT / 'data'
        preferred = data / 'solter.sqlite3'
        previous = data / 'solter-v2.sqlite3'
        return cls(
            telegram_token=os.getenv('TELEGRAM_BOT_TOKEN', '').strip(),
            telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID', '').strip(),
            database_path=previous if not preferred.exists() and previous.exists() else preferred,
        )

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)
