from app.core.dynamo_job_store import DynamoDBJobStore
from app.core.job_store import JobStore
from app.services.batch_processor import BatchProcessor

_job_store: JobStore = DynamoDBJobStore()
_batch_processor: BatchProcessor = BatchProcessor(_job_store)


def get_job_store() -> JobStore:
    return _job_store


def get_batch_processor() -> BatchProcessor:
    return _batch_processor
