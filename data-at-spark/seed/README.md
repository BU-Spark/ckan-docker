# Data@Spark catalog seed

Loads the first catalog entries (`catalog.yaml`, 28 datasets, 3 orgs) into CKAN
through the action API. Idempotent: each dataset is `package_show`n first and
only created or patched when something differs, so re-running reports no changes.

```
pip install requests pyyaml
python test_seed.py                      # manifest checks, no network
export CKAN_URL=http://localhost:5000    # https:// required unless localhost
export CKAN_API_TOKEN=...                # sysadmin token; never committed or logged
python seed.py --dry-run                 # show what would change
python seed.py                           # apply
python seed.py --only 311-service-requests
```

The topic groups (`housing-urban-development`, ...) must already exist (created
by ckanext-spark); the seed only attaches datasets to them.

## Pointer vs hosted

- **hosted** (org `bu-spark`): `storage: pending-osn`, tag `storage-pending`.
  Until OSN storage exists the resource URL points at the current source
  (GitHub tree URL); entries with no GitHub path have no resource yet.
- **pointer** (orgs `analyze-boston-external`, `external-sources`): the
  resource is **format HTML and links to the dataset PAGE**
  (`https://data.boston.gov/dataset/<slug>` for Analyze Boston), **never a
  CSV or other data file**, so DataPusher does not ingest it. `test_seed.py`
  enforces this.

To add an entry, append to `catalog.yaml` and run the self-check.
