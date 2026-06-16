# intern_code_data_debug - 个人知识库

<!-- METADATA:SESSION=1 -->

---

## 知识条目

- `task001_repo_stats_dashboard`: repo stats are served by `dashboard/server.py` at `/repo-stats.json`, refreshed every 12 hours through ECS Exec into the `fetch-gh-data-1` API container using `$DATABASE_URL`, and cached in ignored `dashboard/repo-stats.json`.
- `task002_cephfs_repo_stats_dashboard`: CephFS repo stats use compacted parquet under `/mnt/cephfs/data/processing/github_dl_parquet_compacted`, serve `/cephfs-repo-stats.json`, cache ignored `dashboard/cephfs-repo-stats.json`, and avoid scanning raw `.tgz` archives.
- `task003_compact_repo_stats_layout`: keep large stats panels outside the sticky header; use a normal `repo-stats-area` below search controls with compact, internally scrollable metric grids.
