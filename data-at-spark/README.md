# Data@Spark deployment layer

This directory layers the Data@Spark site onto the official CKAN Compose
deployment without replacing the upstream Compose files.

The image pins:

- CKAN `2.11.5`
- `ckanext-spark` at an immutable Git commit
- `ckanext-s3filestore` at an immutable Git commit (installed unconditionally;
  disabled by default — see below)
- the CKAN 2.11 Solr 9 image

### Updating the `ckanext-spark` pin

Merging a PR to [`ckanext-spark`](https://github.com/BU-Spark/ckanext-spark)
does not deploy anything by itself — the theme/taxonomy extension is pinned to
an exact commit, not a branch, so a push there has no effect here until the
pin is bumped by hand. This is deliberate: a floating reference would let some
unrelated `ckan-docker` change silently drag in unreviewed theme code.

After merging a change in `ckanext-spark`, update its commit SHA in all three
places it's pinned, then commit and push that to this repo's `master`:

- `data-at-spark/Dockerfile` — the `CKANEXT_SPARK_COMMIT` build arg default
- `data-at-spark/compose.yml` — the `DATA_AT_SPARK_EXTENSION_COMMIT` default
- `data-at-spark/.env.example` — the same variable, for local `docker compose
  build`

The push to `master` is what actually triggers the build/publish pipeline
described in "Artifact promotion" below.

## Configure

Create local environment files from the public examples:

```bash
cp .env.example .env
cp data-at-spark/.env.example data-at-spark/.env
```

Replace every credential and secret in `.env`. The Data@Spark file contains
only site and version settings.

`int` is an environment name, not a source branch. Keep the Data@Spark site
URLs aligned with the deployment target:

- `https://int.data.buspark.io` for integration
- `https://data.buspark.io` for production

## Render the merged configuration

```bash
docker compose \
  --env-file .env \
  --env-file data-at-spark/.env \
  -f docker-compose.yml \
  -f data-at-spark/compose.yml \
  config
```

## Build and start

```bash
docker compose \
  --env-file .env \
  --env-file data-at-spark/.env \
  -f docker-compose.yml \
  -f data-at-spark/compose.yml \
  build ckan

docker compose \
  --env-file .env \
  --env-file data-at-spark/.env \
  -f docker-compose.yml \
  -f data-at-spark/compose.yml \
  up
```

These commands describe the Docker Compose baseline. Fedora/rootless Podman
support is tracked separately so runtime-specific differences do not become
Data@Spark application configuration.

## Artifact promotion

The `Build CKAN Docker` workflow publishes the application image to
`ghcr.io/bu-spark/data-at-spark`. A successful push to `master` publishes the
full source Git SHA as a release tag, records its immutable image digest, and
then advances the `integration` tag to that exact digest. The workflow reuses
an existing source-SHA tag only after verifying its provenance. If an earlier
run pushed the image but failed before attestation, a retry rebuilds the
incomplete publication.

**Promoting to production is a separate branch, not a separate build.**
Pushing a `master` commit to `production` (e.g. `git push origin
master:production`, once you've decided that commit is good) runs a
`promote-production` job that **retags** `ghcr.io/bu-spark/data-at-spark:production`
to point at that commit's already-published, already-attested digest -- it
never rebuilds. If the pushed commit was never actually published from
`master` (no existing `:<sha>` tag), the job fails loudly rather than building
something new and unreviewed straight to prod. This mirrors how `:integration`
already works, just gated behind a second, deliberate push instead of firing
on every `master` commit.

### Why the workflow POSTs to the webhook itself

Both `promote-integration` and `promote-production` end with a step that
directly POSTs a signed `registry_package` payload to the host's own
`webhook_listener` endpoint, rather than waiting for GitHub/GHCR to deliver
that event on its own. This is deliberate, not a workaround left in by
accident: confirmed empirically (2026-08-25) that GHCR does not reliably fire
a `registry_package` webhook for a pure retag (`promote-production` is
*always* a retag, so without this step its redeploy would never fire at all),
and even a genuine fresh push isn't guaranteed to fire one either. Self-POSTing
removes the dependency on GHCR's event behavior entirely. The HMAC secret used
lives in this repo's GitHub Actions secrets (`DATA_AT_SPARK_INT_WEBHOOK_SECRET`
/ `DATA_AT_SPARK_PROD_WEBHOOK_SECRET`) and must match whatever
`webhook_listener_secrets` the target host was provisioned with for that hook
ID -- see `ansible/roles/data_at_spark_runtime`.

Publish the package publicly so the host can pull it without a registry
credential. GitHub creates a new container package as private. After the first
successful publish, an organization package administrator must change its
visibility. Do not add a personal access token to this repository to automate
that change.

### If a redeploy reports success but the site is actually down

Confirmed live on both int and prod (2026-08-25): a redeploy that only
recreates the `ckan` service (not the full stack) can leave the rootless
Podman port-forwarder in a broken state -- `podman port` reports the mapping
exists, but nothing is actually listening on the host loopback port, and
Caddy returns 502. `podman ps`/health checks look fine because `ckan` itself
comes up healthy; only the `nginx` container's port publish is affected. The
fix is a full stack reset, not a per-service one:

```bash
podman compose --project-name <project> --env-file .env -f runtime-compose.yml down
podman compose --project-name <project> --env-file .env -f runtime-compose.yml up -d
```

`ss -tlnp | grep <loopback_port>` confirms whether the port is genuinely
bound before assuming a redeploy actually succeeded.

## Single-host runtime automation

The secret-free Ansible scaffold under [`ansible/`](ansible/) renders two
isolated rootless Podman projects behind a host Caddy proxy. It requires a full
Data@Spark image digest and an exact source revision for the nginx and
PostgreSQL support-image builds.

Run its credential-free acceptance check before host work:

```bash
cd data-at-spark/ansible
ansible-playbook playbooks/validate.yml
```

The check renders integration and production in a temporary directory, runs
`podman compose config`, verifies the digest, loopback ports, and volume
contract, then removes its output. The runtime directory also provides
application-consistent backup and isolated restore-test commands; see the
Ansible README for their storage boundary. Registry-tracking redeploy (via
`deploy_tracking_image` on a `data_at_spark_environments` entry, wired for
both int and production as of 2026-08-25 -- see "Artifact promotion" above)
and AWS/Cloudflare/R2 remain the open pieces.

## Local MinIO filestore spike (issue #9, Phase 1)

`ckanext-s3filestore` is installed in the image but is **not enabled** by the
plugin list above. A separate, test-only Compose overlay under
`data-at-spark/minio-spike/` adds a local MinIO backend, enables the plugin,
and configures it with example-only credentials, so it can be exercised
end-to-end without a real cloud account. See
`data-at-spark/minio-spike/README.md` for how to render, start, and validate
it, and for the security boundary and a known upstream streaming/multipart
limitation affecting large interactive web uploads.

## Kubernetes overlays

The Kubernetes deployment overlays live under `data-at-spark/kubernetes/`:

- `data-at-spark/kubernetes/int`
- `data-at-spark/kubernetes/prod`

Render either overlay with `kubectl kustomize`:

```bash
kubectl kustomize data-at-spark/kubernetes/int
kubectl kustomize data-at-spark/kubernetes/prod
```

Both overlays inherit `kubernetes/base` and only layer Data@Spark-specific
identity, namespaces, and environment labels. Their namespaces enforce the
Kubernetes restricted Pod Security profile. Ingress, TLS, storage class, and
secret-manager wiring stay cluster-specific and must be supplied by the
deployment environment.

Before applying either overlay, render it and substitute the exact Data@Spark
image digest produced by the release pipeline:

```bash
kubectl kustomize data-at-spark/kubernetes/int > rendered-int.yaml
python3 kubernetes/substitute_ckan_image.py \
  registry.example/data-at-spark@sha256:DIGEST \
  rendered-int.yaml
```

Use the same immutable image reference for production. The substitution helper
fails unless the reference contains a full SHA-256 digest and exactly the CKAN
web, initialization, and reindex images are replaced.

For a fresh environment, apply the overlay's `namespace.yaml` first, create the
external `ckan-secrets` Secret in that namespace, then apply the promoted
rendered manifest. Do not commit the rendered manifest or Secret values.

<!-- verify self-trigger webhook step, PR #26: 2026-08-26T16:43:54Z -->
