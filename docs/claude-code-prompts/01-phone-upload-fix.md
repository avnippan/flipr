# Phone photo uploads complete (or fail readably)

Goal: a full-size phone photo (5-13 MB) completes a scan, and every failure shows a readable message.

Context: Oct 5 logs show Bedrock Converse rejecting two uploads with ValidationException "image exceeds 5 MB maximum: 7654344 bytes > 5242880 bytes" (raw files were 5.5 MiB and 9.5 MiB; Bedrock measures base64, which is 4/3 of raw, so the raw ceiling is about 3.75 MiB). The S3 retry in process_job.py works. Its wrapper catches every ClientError, so permanent errors are retried and surface as "RetryError[...]".

Do:
1. First, commit the existing S3 retry change in process_job.py on its own ("Retry S3 fetch with backoff to handle upload/processing race") if it's still uncommitted.
2. index.html: before requesting the presigned URL, downscale each image in the browser. Long edge capped at 2000px (never upscale), JPEG at quality 0.85, respect EXIF orientation. If the blob is over 3 MB, re-encode at 0.7, then 0.55. If it still doesn't fit, or decoding fails (e.g. HEIC), show a clear message and don't upload. Upload with Content-Type image/jpeg.
3. process_job.py: after download, if the byte length exceeds 3,670,016 (3.5 MiB), mark the job failed with "Photo too large. Please retry with a smaller photo." and don't call Bedrock. Put the number in a named constant with a comment explaining the 4/3 base64 overhead.
4. Retry predicates in bedrock_vision.py, bedrock_listing.py, and `_get_image_bytes` in process_job.py: check `e.response["Error"]["Code"]`. Bedrock retries only ThrottlingException, ModelTimeoutException, ServiceUnavailableException, InternalServerException, ModelNotReadyException. The S3 fetch retries only NoSuchKey. Use `reraise=True`.
5. Error messages: store a readable message in the job's `error` field (map ValidationException to "We couldn't read that photo"; keep raw details in logs only). The frontend shows it on a failed job, with a "Try again" button that returns to the upload screen.
6. template.yaml: add an S3 lifecycle rule on the images bucket that expires objects under `uploads/` after 7 days (matches the job TTL). Note in your summary that it will delete existing older objects when it takes effect.

Done when:
- `sam build` succeeds.
- From a phone on cellular through the demo, a full-size photo completes; logs show one `bedrock_vision_start` for that job and no ValidationException.
- `scripts/race-test.ps1 -Image <a file over 5 MiB>` ends with status=failed and the "Photo too large" message, with no Bedrock call in the logs. (The script uploads raw bytes and skips the browser resize, so this exercises the worker guard.)
- A text file renamed to .jpg fails with the readable message and no retries.

Out of scope: server-side resizing, multi-photo, a start-call endpoint, any redesign.

Show the diff for each file separately before applying. Don't push.
