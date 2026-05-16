import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

import boto3
from botocore.config import Config
import structlog
from boto3.dynamodb.conditions import Key
from decimal import Decimal
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from app.config import settings
from app.core.job_models import (
    Job, JobStatus, ItemResult, ItemStatus
)
from app.core.job_store import JobStore

logger = structlog.get_logger(__name__)

TTL_DAYS = 7

# Dedicated executor isolates DynamoDB threads from FastAPI's shared default pool.
_dynamo_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dynamo")


class DynamoDBJobStore(JobStore):

    @staticmethod
    def _to_dynamodb(obj):
        """Recursively convert floats to Decimal for DynamoDB compatibility."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: DynamoDBJobStore._to_dynamodb(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [DynamoDBJobStore._to_dynamodb(i) for i in obj]
        return obj

    @staticmethod
    def _make_table():
        # Called inside the executor thread so the boto3 session, urllib3 pool,
        # and SSL context are all created and used on the same thread.
        config = Config(
            connect_timeout=5,
            read_timeout=5,
            retries={'max_attempts': 1}
        )
        resource = boto3.resource(
            'dynamodb',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            verify=False,
            config=config
        )
        return resource.Table(settings.dynamodb_table_name)

    async def _run(self, fn: Callable[..., Any]) -> Any:
        """Run fn(table) on the dedicated DynamoDB thread pool."""
        loop = asyncio.get_running_loop()

        def _in_thread():
            return fn(self._make_table())

        return await loop.run_in_executor(_dynamo_executor, _in_thread)

    @staticmethod
    def _ttl() -> int:
        return int(time.time()) + (TTL_DAYS * 24 * 60 * 60)

    @staticmethod
    def _job_pk(job_id: str) -> str:
        return f"JOB#{job_id}"

    @staticmethod
    def _item_sk(item_index: int) -> str:
        return f"ITEM#{item_index}"

    async def create_job(self, total_items: int, metadata: dict | None = None) -> Job:
        job = Job(
            total_items=total_items,
            metadata=metadata or {},
            items=[
                ItemResult(index=i, status=ItemStatus.PENDING)
                for i in range(total_items)
            ],
        )

        pk = self._job_pk(job.job_id)
        ttl = self._ttl()

        await self._run(lambda table: table.put_item(Item={
            'PK': pk,
            'SK': 'META',
            'job_id': job.job_id,
            'status': job.status.value,
            'total_items': job.total_items,
            'metadata': job.metadata,
            'created_at': job.created_at.isoformat(),
            'completed_at': None,
            'ttl': ttl,
        }))

        for item in job.items:
            await self._run(lambda table, i=item: table.put_item(Item={
                'PK': pk,
                'SK': self._item_sk(i.index),
                'index': i.index,
                'status': i.status.value,
                'result': None,
                'error': None,
                'created_at': i.created_at.isoformat(),
                'completed_at': None,
                'ttl': ttl,
            }))

        logger.info("job_created", job_id=job.job_id, total_items=total_items)
        return job

    async def get_job(self, job_id: str) -> Job | None:
        pk = self._job_pk(job_id)

        response = await self._run(
            lambda table: table.query(
                KeyConditionExpression=Key('PK').eq(pk)
            )
        )

        if not response['Items']:
            return None

        items = response['Items']
        meta = next(item for item in items if item['SK'] == 'META')
        item_rows = [item for item in items if item['SK'].startswith('ITEM#')]

        reconstructed_items = sorted([
            ItemResult(
                index=int(row['index']),
                status=ItemStatus(row['status']),
                result=row.get('result'),
                error=row.get('error'),
                created_at=datetime.fromisoformat(row['created_at']),
                completed_at=datetime.fromisoformat(row['completed_at']) if row.get('completed_at') else None,
            )
            for row in item_rows
        ], key=lambda x: x.index)

        job = Job(
            job_id=meta['job_id'],
            status=JobStatus(meta['status']),
            total_items=int(meta['total_items']),
            metadata=meta.get('metadata', {}),
            created_at=datetime.fromisoformat(meta['created_at']),
            completed_at=datetime.fromisoformat(meta['completed_at']) if meta.get('completed_at') else None,
            items=reconstructed_items,
        )

        logger.info("job_fetched", job_id=job_id, status=job.status)
        return job

    async def update_job(
        self,
        job_id: str,
        status: JobStatus | None = None,
        items: list[ItemResult] | None = None,
    ) -> Job:
        pk = self._job_pk(job_id)
        update_parts = []
        expr_names = {'#s': 'status'}
        expr_values = {}

        if status is not None:
            update_parts.append('#s = :status')
            expr_values[':status'] = status.value
            if status == JobStatus.COMPLETED:
                update_parts.append('completed_at = :completed_at')
                expr_values[':completed_at'] = datetime.utcnow().isoformat()

        if update_parts:
            expr = 'SET ' + ', '.join(update_parts)
            await self._run(lambda table: table.update_item(
                Key={'PK': pk, 'SK': 'META'},
                UpdateExpression=expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            ))

        logger.info("job_updated", job_id=job_id, status=status.value if status else None)

        job = await self.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        return job

    async def update_item(
        self,
        job_id: str,
        item_index: int,
        status: ItemStatus,
        result: dict | None = None,
        error: str | None = None,
    ) -> Job:
        pk = self._job_pk(job_id)
        item_sk = self._item_sk(item_index)

        update_parts = ['#s = :status']
        expr_values = {':status': status.value}
        expr_names = {'#s': 'status'}

        if result is not None:
            expr_names['#r'] = 'result'
            update_parts.append('#r = :result')
            expr_values[':result'] = self._to_dynamodb(result)
        if error is not None:
            expr_names['#e'] = 'error'
            update_parts.append('#e = :error')
            expr_values[':error'] = error
        if status in (ItemStatus.SUCCESS, ItemStatus.FAILED):
            update_parts.append('completed_at = :completed_at')
            expr_values[':completed_at'] = datetime.utcnow().isoformat()

        item_expr = 'SET ' + ', '.join(update_parts)
        await self._run(lambda table: table.update_item(
            Key={'PK': pk, 'SK': item_sk},
            UpdateExpression=item_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        ))

        response = await self._run(
            lambda table: table.query(
                KeyConditionExpression=Key('PK').eq(pk) & Key('SK').begins_with('ITEM#')
            )
        )
        item_statuses = [ItemStatus(item['status']) for item in response['Items']]

        if not item_statuses:
            raise ValueError(f"Job {job_id} not found")

        terminal = {ItemStatus.SUCCESS, ItemStatus.FAILED}
        if all(s in terminal for s in item_statuses):
            new_job_status = JobStatus.COMPLETED
        elif any(s == ItemStatus.PROCESSING for s in item_statuses):
            new_job_status = JobStatus.PROCESSING
        else:
            new_job_status = None

        if new_job_status is not None:
            job_update_parts = ['#s = :status']
            job_expr_names = {'#s': 'status'}
            job_expr_values = {':status': new_job_status.value}
            if new_job_status == JobStatus.COMPLETED:
                job_update_parts.append('completed_at = :completed_at')
                job_expr_values[':completed_at'] = datetime.utcnow().isoformat()

            job_expr = 'SET ' + ', '.join(job_update_parts)
            await self._run(lambda table: table.update_item(
                Key={'PK': pk, 'SK': 'META'},
                UpdateExpression=job_expr,
                ExpressionAttributeNames=job_expr_names,
                ExpressionAttributeValues=job_expr_values,
            ))

        logger.info(
            "item_updated",
            job_id=job_id,
            item_index=item_index,
            item_status=status.value,
            job_status=new_job_status.value if new_job_status else None,
        )

        job = await self.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        return job