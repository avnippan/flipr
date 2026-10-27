<#
Controlled upload-race test for FLIPR.
Creates a job, waits -DelaySeconds, uploads -Image, then polls status every 3s.

Usage (repo root):
  .\scripts\race-test.ps1 -Image .\item_images\mens\tops\t_shirts\wrangler-007.jpg -DelaySeconds 10

Then check the retries:
  aws logs tail /aws/lambda/flipr-process-job-dev --since 3m --filter-pattern "s3_get_retry"

Notes:
- Uploads raw bytes (no browser resize). A file over 3.5 MiB exercises the worker size guard.
- POST /jobs requires the invite code (SSM /flipr/invite-code). Pass it with -InviteCode, e.g. -InviteCode "your-code".
  Without it, or with a wrong one, the API returns 401. After DAILY_JOB_LIMIT jobs in a UTC day it returns 429.
#>
param(
  [Parameter(Mandatory = $true)][string]$Image,
  [int]$DelaySeconds = 10,
  [string]$Api = "https://bvl98eh3bf.execute-api.us-east-1.amazonaws.com/dev",
  [string]$InviteCode = ""
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Image)) { throw "Image not found: $Image" }

$sizeMiB = [math]::Round((Get-Item $Image).Length / 1MB, 2)
$headers = @{}
if ($InviteCode) { $headers["X-Invite-Code"] = $InviteCode }

$r = Invoke-WebRequest -Uri "$Api/jobs" -Method POST -ContentType "application/json" -Body '{"marketplace":"ebay"}' -Headers $headers -UseBasicParsing
$job = $r.Content | ConvertFrom-Json
$t0 = Get-Date
function Elapsed { [int]((Get-Date) - $t0).TotalSeconds }

"job_id: $($job.job_id)  image: $sizeMiB MiB"
Start-Sleep -Seconds $DelaySeconds
# Presigned POST: send the policy fields, then the file (curl.exe handles multipart on Windows PowerShell 5 too)
$formArgs = @()
foreach ($p in $job.upload_fields.PSObject.Properties) { $formArgs += "-F"; $formArgs += "$($p.Name)=$($p.Value)" }
& curl.exe -sS --fail @formArgs -F "file=@$Image;type=image/jpeg" $job.upload_url | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Upload failed (curl exit $LASTEXITCODE). S3 rejects files over the size limit." }
"uploaded at +$(Elapsed)s"

for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 3
  $s = (Invoke-WebRequest -Uri "$Api/jobs/$($job.job_id)" -UseBasicParsing).Content | ConvertFrom-Json
  "+$(Elapsed)s status=$($s.status) error=$($s.error)"
  if ($s.status -in "complete", "failed") { break }
}
