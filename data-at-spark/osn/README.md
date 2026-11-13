# Data@Spark resource storage on OSN (issue #9, Phase 2 retargeted)

Decision of 2026-11-13 (Langdon, Mitchell Horn/CDS): Data@Spark stores resource
bytes on the **Open Storage Network** (S3-compatible, ACCESS-allocated, MGHPCC
pod) instead of Cloudflare R2. Mitchell owns the allocation (10 TB, rising to
20 TB). OSN gives us two buckets:

| Bucket  | Anonymous read | Keys |
|---------|----------------|------|
| PUBLIC  | yes, by design | write key held by the curator only |
| PRIVATE | no             | read-only key, read/write key |

FERPA/HIPAA or otherwise restricted data never goes to either bucket.

This directory is ready to run as soon as keys arrive. It holds no
credentials, bucket names or real endpoints. They arrive out of band (Slack)
and live only in `data-at-spark/osn/.env`, which is gitignored.

## What Phase 1 (minio-spike) actually proved

From `../minio-spike/README.md` ("Verified behavior", 2026-07-24, CKAN 2.11.5)
and `../minio-spike/validate.py`:

- **Proved end-to-end against local MinIO:** a caller-chosen resource UUID
  works. Object-first-then-`resource_create` avoids the DataPusher placeholder
  race. Anonymous CKAN access to a private dataset returns 401 and anonymous
  direct bucket access returns 403. Authorized download redirects to a
  short-lived signed URL with a matching SHA-256. DataPusher loads a private
  CSV through the CKAN download path. A forced registration failure deletes
  the uploaded object.
- **Proved only statically** (`validate.py` checks text, not behavior): the
  extension pin, `acl=private`, path-style addressing, and that no `.env` file
  or k8s `Secret` is committed.
- **Not proved:** any real remote endpoint, real TLS, region handling,
  multi-GB uploads through CKAN's web form, multipart cleanup, request counts.
- **Known blockers carried forward:**
  1. `upload_to_key` reads the whole upload into CKAN worker memory. Large
     files must go through `direct_upload.py`, which does a boto3 multipart
     upload.
  2. `resource_delete` does **not** delete the object. With a fixed 10-20 TB
     quota this now has a direct cost: orphans use up the allocation.

## Recommended design

1. **All CKAN uploads go to the PRIVATE bucket** through `ckanext-s3filestore`
   with `acl=private` and SigV4 signed URLs (60 s), path-style. This applies to
   resources in *public* datasets too. CKAN checks authorization first and then
   redirects to a signed URL, as the spike proved. CKAN holds **only the
   private read/write key**.
2. **The PUBLIC bucket is not a CKAN filestore.** It holds large, openly
   licensed datasets that a curator deliberately publishes there (rclone or
   aws-cli with the public-bucket write key, outside CKAN). In CKAN they are
   registered as ordinary **URL resources** (`url_type` empty) pointing at the
   anonymous object URL. CKAN never holds a key for this bucket.
3. **Large uploads to the private bucket** use `../minio-spike/direct_upload.py`
   unchanged (multipart, object-first, rollback on registration failure). It
   reads the same `CKANEXT__S3FILESTORE__*` variables this overlay sets. The web
   form stays limited to small files until the buffering blocker is fixed.

Why not a second s3filestore storage path for the public bucket: at the pinned
commit `ckanext-s3filestore` takes exactly one bucket and one global
`acl` (the default is `public-read`, so `private` must always be set
explicitly). A per-dataset public/private route would need a fork. We would
also gain nothing: a public dataset in the private bucket is already served to
everyone through CKAN-issued signed URLs. A second route would only bring back
the risk that a dataset made private later is still anonymously readable.

### Resolving the #9 criterion "no object is anonymously readable"

That criterion conflicts with OSN's public bucket, which exists so that objects
are anonymously readable. Resolution: **narrow the criterion to the store CKAN
manages and add one rule for the public bucket.**

- Proposed wording: *"No object in the CKAN-managed (private) bucket is
  anonymously readable, including resources in public datasets. The public OSN
  bucket holds only open-licensed data a curator published there on purpose.
  CKAN has no credentials for it, and no private CKAN dataset may reference
  it."*
- What still holds unchanged: the signed-URL-after-authorization criterion,
  the checksum criterion, the DataPusher criteria, and the rule that every
  upload through CKAN stays private.
- New risk this creates: CKAN privacy cannot protect a public-bucket URL. If
  a dataset that links to the public bucket is made private, the bytes stay
  public. The Phase 2 checklist adds a check for this. Anything that might ever
  become private belongs in the private bucket.
- Needs sign-off from Langdon (the #9 author) before this is treated as
  accepted. Edit the issue rather than this file.

## Mapping ckanext-s3filestore config to OSN

`compose.yml` here mirrors `../minio-spike/compose.yml` without the MinIO
services. Every OSN value uses `${VAR:?}`, so a missing value fails at
`compose config` time.

| CKAN env var (ckanext-envvars)               | Source (`osn/.env`)                | Notes |
|---------------------------------------------|------------------------------------|-------|
| `CKANEXT__S3FILESTORE__HOST_NAME`            | `OSN_ENDPOINT_URL`                 | `https://<pod-host>`, no bucket in path |
| `CKANEXT__S3FILESTORE__REGION_NAME`          | `OSN_REGION`                       | OSN publishes none; try `us-east-1` (UNVERIFIED) |
| `CKANEXT__S3FILESTORE__AWS_BUCKET_NAME`      | `OSN_PRIVATE_BUCKET`               | private bucket only |
| `CKANEXT__S3FILESTORE__AWS_ACCESS_KEY_ID`    | `OSN_PRIVATE_RW_ACCESS_KEY_ID`     | read/write key |
| `CKANEXT__S3FILESTORE__AWS_SECRET_ACCESS_KEY`| `OSN_PRIVATE_RW_SECRET_ACCESS_KEY` | read/write key |
| `CKANEXT__S3FILESTORE__ADDRESSING_STYLE`     | fixed `path`                       | `direct_upload.py` also hard-codes path-style |
| `CKANEXT__S3FILESTORE__SIGNATURE_VERSION`    | fixed `s3v4`                       | |
| `CKANEXT__S3FILESTORE__ACL`                  | fixed `private`                    | never omit: the default is `public-read` |
| `CKANEXT__S3FILESTORE__SIGNED_URL_EXPIRY`    | `S3FILESTORE_SIGNED_URL_EXPIRY`    | 60 s, as in the spike |

`ckanext.s3filestore.aws_storage_path` is deliberately left unset. The
extension stores objects at `<aws_storage_path>/resources/<id>/<file>`, but
`direct_upload.py` hard-codes `resources/<id>/<file>`. If CDS later shares the
bucket and we need a prefix, update both together.

At startup the extension calls `HeadBucket` and calls `CreateBucket` if it gets
a 404. On OSN, buckets are created by the OSN admins, not by us, so a wrong
bucket name or a key without `HeadBucket` permission fails at boot (403
raises `S3FileStoreException`). That failure is the intended behavior.

## Run (once keys exist)

From the repository root:

```bash
cp .env.example .env
cp data-at-spark/osn/.env.example data-at-spark/osn/.env   # fill in from Slack; never commit
set -a; . data-at-spark/osn/.env; set +a

das_osn=(docker compose --env-file .env
  -f docker-compose.yml -f data-at-spark/compose.yml -f data-at-spark/osn/compose.yml)
"${das_osn[@]}" config   # fails loudly if any OSN_* value is missing
"${das_osn[@]}" up -d --build
```

Export the variables into the shell as shown. With Compose v5.4.0, stacking
`--env-file data-at-spark/osn/.env` did not override `CKAN__PLUGINS`, so
s3filestore stayed disabled. Static checks (pin, no tracked
`data-at-spark/osn/.env`, no k8s Secret in this overlay) reuse the spike's
validator: `python3 data-at-spark/minio-spike/validate.py`.

Do not bring this stack up on a machine where port 5000 or the
`ckanext-spark-dev-*` containers are in use.

## What changes vs the R2 plan

- **Bucket ownership:** R2 Phase 2 assumed a disposable bucket we create. OSN
  buckets are fixed, admin-created and shared with Mitchell's allocation, so
  Phase 2 uses a **test prefix** in the private bucket and cleans it up by
  prefix. CKAN must never create or delete buckets.
- **Two buckets with different trust levels** instead of one private R2
  bucket. This is the source of the #9 tension resolved above.
- **Region:** R2 uses `auto`. OSN documents none (UNVERIFIED, see below).
- **Addressing:** R2 supports both styles. For OSN, path-style is what the
  docs' URL form implies.
- **Cost model:** no egress or request billing. The limit is a hard capacity
  quota, which makes the `resource_delete` orphan problem a capacity problem.
- **Keys:** issued through ColdFront (`coldfront.osn.mghpcc.org`) by the
  allocation's data manager instead of scoped R2 API tokens. Key scope is
  per bucket, not per prefix.
- **Network:** traffic to the MGHPCC pod goes over research/education
  networks. Throughput from the production VM is unmeasured.

## OSN facts: verified vs UNVERIFIED

Sources: openstoragenetwork.readthedocs.io (system overview, accessing
datasets) and coldfront.osn.mghpcc.org gen2docs (read-only keys), read
2026-11-13.

Verified from public docs:
- S3-convention REST API, accessed with access/secret key pairs. Open-access
  datasets are anonymously readable and protected datasets need keys.
- Allocations run from 10 TB to 50 TB through ACCESS/XRAS. Retention is up to
  5 years for active research data, and usage falls under the OSN Acceptable
  Use Policy.
- Endpoint examples: `https://mghp.osn.xsede.org` (legacy) and
  `https://<site>.osn.mghpcc.org/<bucket>` (bucket in the path).
- Bucket access is controlled by bucket policies on a Ceph RADOS Gateway.
  Anonymous read is a policy statement with principal `"AWS": ["*"]`.
  Read-only access is a dedicated `<bucket>_readonly` user with `s3:GetObject`
  and `s3:ListBucket`.
- rclone against OSN needs `provider = Ceph` and `no_check_bucket = true`.

UNVERIFIED (check during Phase 2 step 1, or ask Mitchell):
- The exact endpoint hostname for our pod/allocation.
- Whether virtual-host addressing works. Path-style is assumed.
- The region string the RGW expects for SigV4.
- Presigned GET URL support, and the maximum expiry the RGW honors.
- Multipart limits (part size, part count, maximum object size), and whether
  incomplete multipart uploads count toward the quota or are expired by a
  lifecycle rule.
- Whether the bucket CORS configuration can be set, and who sets it. It is not
  needed for redirect downloads, only for browser-side fetches or uploads.
- Whether the public bucket also allows anonymous **ListBucket**, which would
  make every key in it discoverable.
- Whether our keys can call `HeadBucket`, `PutBucketPolicy` and
  `PutBucketLifecycle`.
- Durability, replication and backup. No public statement was found, so
  assume **no backup**.

## Open questions for Mitchell / Langdon

1. Endpoint hostname and region string for our buckets? Path-style only?
2. Who holds the public-bucket write key? Proposal: one named curator, never
   CKAN.
3. Does the public bucket allow anonymous ListBucket, or only GetObject?
4. Do our keys allow `HeadBucket` (needed at CKAN boot), and can we set CORS
   or lifecycle rules (expire incomplete multipart uploads), or does OSN staff
   do it?
5. Is there any backup or replication? If not, what is the recovery story for
   a deleted bucket or object?
6. Who else holds keys to the private bucket (CDS data managers, OSN admins)?
   That set of people is the real trust boundary for "private" CKAN data.
7. If CKAN later serves CDS data, should it be the same buckets under a
   prefix (this requires the `aws_storage_path` change above) or separate
   buckets?
8. Is the #9 criterion rewording above acceptable to Langdon?
9. What is the policy for objects orphaned by `resource_delete`: a purge job,
   a retention period, or a quota alert threshold?
10. What does OSN's AUP and allocation require for public-facing data (for
    example licensing or attribution on the public bucket)?

## Phase 2 checklist (OSN), ready to run when keys arrive

Run from a disposable local stack. Use only a `phase2-test/`-style dataset
naming, then delete everything it created. Record results in issue #9.

1. **Connectivity:** with the private RW key, `aws s3api head-bucket` and
   `list-objects-v2 --max-items 1` against `OSN_ENDPOINT_URL` path-style. Try
   `OSN_REGION=us-east-1`; on a signature error, record the region the error
   reports and use it.
2. **Key scopes:** the private RO key can GET but cannot PUT or DELETE. The
   private RW key can PUT, GET and DELETE. With no key, a GET on a private
   object returns 403.
3. **Public bucket:** an anonymous GET of a curator-uploaded test object
   returns 200. Record whether an anonymous list works. Confirm the CKAN RW key
   **cannot** write to the public bucket. If it can, stop and raise it: CKAN
   would then be able to publish anonymously.
4. **CKAN boot:** `"${das_osn[@]}" up`, then confirm s3filestore loads and
   `HeadBucket` succeeds.
5. **Small web upload** to a private dataset: anonymous CKAN returns 401. The
   direct object URL with no signature returns 403. Authorized download
   redirects to a signed URL, the SHA-256 matches, and the URL fails after
   expiry.
6. **Public dataset:** after publishing it, anonymous CKAN download still goes
   through a signed URL. The unsigned object URL still returns 403.
7. **DataPusher:** a small private CSV loads into DataStore through the CKAN
   download path.
8. **Large file:** `direct_upload.py` with a multi-GB file. Record the
   multipart part size and count, then check the SHA-256 after download.
9. **Failed multipart:** interrupt an upload. `list-multipart-uploads` shows
   it. `abort-multipart-upload` clears it. Record whether a lifecycle rule
   exists to do this automatically.
10. **Registration failure:** `CKAN_TEST_FORCE_REGISTRATION_FAILURE=1` and
    confirm the object is removed.
11. **Delete:** `resource_delete`, then check whether the object remains (it is
    expected to). Delete it manually and record that it is gone.
12. **Public-link guard:** confirm that no private dataset has a resource URL
    on the public bucket host/bucket (`package_search` with
    `include_private=true`, then a grep of resource URLs).
13. **Record:** request counts, upload/download throughput from the
    deployment host, and any quota behavior seen.
14. **Cleanup:** delete the test prefix and test datasets, `docker compose
    down -v`, then rotate any key that was pasted anywhere except `osn/.env`.
