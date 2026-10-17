"""The operator configures Telegram only. Scheduling budgets are internal."""

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    token: str
    chat_id: str
    database: Path

    @classmethod
    def load(cls) -> 'Settings':
        load_dotenv(ROOT / '.env')
        # Keep the historic filename to preserve alert deduplication on upgrade.
        data_file = ROOT / 'data' / 'solter-v2.sqlite3'
        return cls(
            token=os.getenv('TELEGRAM_BOT_TOKEN', '').strip(),
            chat_id=os.getenv('TELEGRAM_CHAT_ID', '').strip(),
            database=data_file,
        )

    @property
    def telegram_ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def diagnostic(self) -> dict:
        return {
            'telegram_configured': self.telegram_ready,
            'database': str(self.database),
            'scanner': 'market momentum (single engine)',
            'requires_rpc': False,
            'requires_registration': False,
            'automatic_trading': False,
            'milestone_tracking': False,
        }
