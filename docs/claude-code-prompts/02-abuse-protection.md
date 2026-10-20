# Basic abuse protection before sharing the link

Goal: a stranger or script can't run up the Bedrock bill. This is a speed bump, not real auth.

Context: POST /jobs is public. Each job costs roughly 2 cents. The invite code will sit in browser storage, so treat it as a deterrent only.

Do:
1. create_job.py: require header `X-Invite-Code` to match an SSM String parameter `/flipr/invite-code` (read once per cold start, compare with `hmac.compare_digest`). Missing or wrong returns 401 with a readable JSON message.
2. Daily cap: count job creations per UTC day in the existing rate-limits table with an atomic conditional increment (same pattern as post_to_ebay), key prefix `JOBS#`. Limit comes from env var `DAILY_JOB_LIMIT` (default 50). Over the limit returns 429 "Daily limit reached. Try again tomorrow."
3. template.yaml: the SSM reference, the env var, the read policy for the function, and API Gateway stage throttling (burst and rate; propose values and say why).
4. index.html: ask for the invite code once, store it in localStorage, send it on job creation. Show the 401 and 429 messages readably.
5. Update scripts/race-test.ps1 usage notes if needed (it already accepts `-InviteCode`).

Done when:
- curl without the header -> 401; with the header -> 202; request number DAILY_JOB_LIMIT+1 -> 429.
- The demo works with the code and shows a clear message without it.

Out of scope: real user accounts, OAuth, anything per-user.

Manual step for me, not code: create an AWS Budgets monthly budget with email alerts at 50%, 80%, and 100%. Verify with `aws budgets describe-budgets --account-id <id>`.

Show the diff before applying. Don't push.
