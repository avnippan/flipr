import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

class ItemStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"

class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ItemResult(BaseModel):
    index: int
    status: ItemStatus
    result: dict | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class Job(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.PENDING
    total_items: int
    items: list[ItemResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    @property
    def progress(self) -> dict:
        completed = sum(
            1 for item in self.items
            if item.status in (ItemStatus.SUCCESS, ItemStatus.FAILED)
        )
        total = self.total_items
        return {
            "completed": completed,
            "total": total,
            "percentage": (completed / total * 100) if total > 0 else 0.0,
        }
