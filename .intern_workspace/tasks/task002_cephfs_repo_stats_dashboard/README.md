<!-- METADATA:STATUS=Completed,ASSIGNEE=intern_code_data_debug -->

# task002_cephfs_repo_stats_dashboard

## Goal

Add CephFS repository collection statistics to the kanban dashboard.

## Requirements

- Refresh CephFS repository statistics when the AWS repository statistics refresh.
- Use `/mnt/cephfs/data/processing/github_dl_parquet_compacted` as the CephFS statistics source.
- Do not scan raw `.tgz` archive directories for the dashboard refresh.
- Display the CephFS statistics as a separate section below `Repository Collection`.
- Name the section `CEPHFS Repository Collection`.
- Bucket repositories by `stargazer_count`:
  - `[0, 5)`
  - `[5, 10)`
  - `>= 10`
  - `unknown` when star count is unavailable.
- Include archive processing status counts from the compacted manifest when available.

## Notes

- The compacted parquet `meta` dataset contains `stargazer_count`.
- The compacted parquet `manifest` dataset contains archive `status` and `meta_row_count`.

## PR

- https://github.com/songCNMS/code-data-kanban/pull/3

## Completion

- Completed and approved for merge on 2026-06-16.
