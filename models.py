from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Hackathon(BaseModel):
    source: str                      # e.g. "devpost"
    source_id: str                   # unique id/slug from the source, used for dedupe
    title: str
    url: str
    thumbnail_url: Optional[str] = None

    mode: Optional[str] = None       # "online" | "offline" | "hybrid" | None if unknown
    location: Optional[str] = None

    deadline: Optional[datetime] = None      # submission deadline
    prize_amount: Optional[float] = None      # normalized numeric prize (best guess)
    prize_text: Optional[str] = None          # raw prize string, e.g. "$50,000 in prizes"

    themes: list[str] = Field(default_factory=list)   # e.g. ["Machine Learning", "Web"]
    participants: Optional[int] = None

    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        extra = "ignore"
