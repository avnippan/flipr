# Phone photo uploads complete (or fail with a readable message)

Goal: a full-size photo from an iPhone or Android (JPEG or HEIC) completes a scan, and every failure shows a readable message with a way to retry.

Context: Oct 5 logs show Bedrock Converse rejecting two uploads with ValidationException "image exceeds 5 MB maximum: 7654344 bytes > 5242880 bytes" (raw files were 5.5 MiB and 9.5 MiB). Bedrock measures base64, which is 4/3 of raw, so the raw ceiling is about 3.75 MiB. The S3 retry in process_job.py is already committed in 1fe4301. Don't commit it again. Its wrapper catches every ClientError, so permanent errors are retried and surface as "RetryError[...]". Most users are on iPhone (HEIC by default unless set to Most Compatible) or Android.

Do:
1. index.html, file picker: set `accept="image/*"`. Don't restrict to JPEG, or iPhone will block HEIC before the browser can convert it.
2. index.html, resize: for each picked file, decode with `createImageBitmap(file, { imageOrientation: 'from-image' })`. If decoding fails, show "We couldn't read this photo. Try a JPEG, or take a screenshot of it and upload that." and don't upload that file.
3. index.html, resize: draw to a canvas with the long edge capped at 2000px (never upscale) and export as JPEG at quality 0.85. If the blob is over 3 MB, re-encode at 0.7, then 0.55. If it still doesn't fit, show the same readable message.
4. index.html, upload: send the resized blob with Content-Type image/jpeg. Resize is the only path to the worker, so Bedrock only ever receives a JPEG under the limit.
5. process_job.py, size guard: after download, if the byte length exceeds 3,670,016 (3.5 MiB), mark the job failed with "Photo too large. Please retry with a smaller photo." and don't call Bedrock. Put the number in a named constant with a comment explaining the 4/3 base64 overhead.
6. Retry predicates in bedrock_vision.py, bedrock_listing.py, and `_get_image_bytes` in process_job.py: check `e.response["Error"]["Code"]`. Bedrock retries only ThrottlingException, ModelTimeoutException, ServiceUnavailableException, InternalServerException, ModelNotReadyException. The S3 fetch retries only NoSuchKey. Use `reraise=True` on all three. This also fixes the Bugbot finding on process_job.py.
7. Error messages: store a readable message in the job's `error` field. Map ValidationException to "We couldn't read that photo" and keep raw details in logs only. The frontend shows that message on a failed job, with a "Try again" button that returns to the upload screen.
8. template.yaml: add an S3 lifecycle rule on the images bucket that expires objects under `uploads/` after 7 days (matches the job TTL). Note in your summary that it will delete existing older objects when it takes effect.
9. Avoid CSS `aspect-ratio` in any new layout (iOS Safari bug). Use the padding-top trick.

Done when:
- `sam build` succeeds.
- A full-size iPhone photo, Safari, cellular, default camera settings, completes. Logs show one `bedrock_vision_start` for that job and no ValidationException.
- An Android photo in Chrome on cellular completes.
- A HEIC photo that the browser can't decode shows the readable message, with no upload.
- `scripts/race-test.ps1 -Image <a file over 5 MiB>` ends with status=failed and "Photo too large", with no Bedrock call in the logs. (The script uploads raw bytes and skips the browser resize, so this exercises the worker guard.)
- A text file renamed to .jpg fails with the readable message and no retries.

Out of scope: server-side HEIC conversion, multi-photo per item, a start-call endpoint, per-photo status UI (follow-up prompt), any redesign.

Show the diff for each file separately before applying. Don't push.