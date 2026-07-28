# Data@Spark runtime automation

This is the narrow runtime layer for the single-host deployment described in
the orchestration repository. It excludes AWS, DNS,
Cloudflare, R2, or credentials.

Each environment has its own checked-out runtime source tree, Compose project,
named volumes, and loopback port. The role verifies that each source checkout
is at `data_at_spark_runtime_source_sha`; this source is used only for the
locally built nginx and PostgreSQL support services. Its standalone deployment
Compose file omits the upstream `pip_cache` and `site_packages`
mounts, so no mutable package volume masks the digest-pinned CKAN image. CKAN
itself uses `ghcr.io/bu-spark/data-at-spark@sha256:<64 hex
characters>`.

Real inventories and `data_at_spark_secrets` stay outside Git. The role writes
them to the managed host's `.env` with mode `0600`. Git contains no values.
repository. A production Caddyfile must import the rendered Caddy fragment.

Validate the contract without a managed host or credentials:

```bash
ansible-playbook -i inventories/example/hosts.yml playbooks/validate.yml
```

The validation playbook renders both environments in a temporary directory,
runs `podman compose config` for each one, checks the image, port, and volume
contract, and then removes the temporary output. It does not start containers,
contact a registry, or modify Caddy/systemd.

For a host run, create an external inventory that sets non-example source
checkouts, image digests, and every `data_at_spark_secrets` key. The host must
already have the `dataspark` user, rootless Podman, a working `podman compose`,
Caddy, lingering enabled for the deploy user, and source trees checked out at
the exact pinned revision.
After reviewing rendered files, opt in to unit activation with
`data_at_spark_runtime_manage_systemd: true`; Caddy integration remains a separate
host-level action. Caddy proxies to nginx over its loopback-only self-signed
TLS listener and disables upstream certificate verification only for that local
hop. The runtime service builds only the nginx and PostgreSQL
support images from its verified checkout; the CKAN service has no build stanza
and remains digest-pinned.
