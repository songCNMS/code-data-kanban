<!-- METADATA:SESSION=0 -->

# Task Knowledge

- `/mnt/cephfs/data/processing/github_dl_parquet_compacted/meta` has parquet repo metadata with `stargazer_count`.
- `/mnt/cephfs/data/processing/github_dl_parquet_compacted/manifest` has parquet archive status rows with `status` and `meta_row_count`.
- Prior probe found compacted meta rows: 3,900,445; manifest rows: 3,900,514; `status=ok`: 3,900,369.
- Verified at 2026-06-16T08:16:04Z: ok repos 3,900,369; stars `[0, 5)` 1,081,694; `[5, 10)` 352,273; `>= 10` 2,466,402; unknown 0.
- Dashboard endpoint is `/cephfs-repo-stats.json`; runtime cache is ignored `dashboard/cephfs-repo-stats.json`.
