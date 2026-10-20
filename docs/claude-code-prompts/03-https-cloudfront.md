# Serve the demo over HTTPS

Goal: the demo opens with a padlock instead of "Not Secure".

Context: the demo is an S3 static website (HTTP only). The bucket was created by CLI and is not in the stack. Don't import or modify it.

Do:
1. template.yaml: add a CloudFront distribution. Origin is the S3 website endpoint `flipr-demo-821122422427.s3-website-us-east-1.amazonaws.com` as a custom origin with OriginProtocolPolicy `http-only` (website endpoints don't speak HTTPS). Viewer protocol policy redirect-to-https, default CloudFront certificate, default root object index.html, compression on, short default TTL (60s) because index.html changes often. Output the distribution domain.
2. Check the images bucket CORS. If allowed origins list the old S3 website URL, add the CloudFront domain.
3. Tell me the permissions the deploy user needs. It was at its managed-policy limit before, so suggest a customer-managed or inline policy rather than another attachment.

Done when:
- `curl -I https://<domain>/` returns 200.
- A full scan works from a phone through the https URL (presigned S3 upload and API calls still work).

After deploy (my steps): update the README demo link; after each frontend change run `aws s3 cp ...` then `aws cloudfront create-invalidation --distribution-id <id> --paths "/*"`.

Out of scope: custom domain, WAF.

Show the diff before applying. Don't push.
