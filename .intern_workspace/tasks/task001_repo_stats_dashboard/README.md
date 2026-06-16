<!-- METADATA:STATUS=Open,ASSIGNEE= -->

# task001_repo_stats_dashboard

## Goal

Add repository collection statistics to the kanban dashboard.

## Requirements

- Refresh repository statistics every 12 hours.
- Show the statistics section directly below the dashboard search controls.
- Bucket repositories with `status = 'succeeded'` by stars:
  - `[0, 5)`
  - `[5, 10)`
  - `>= 10`
  - `unknown` when the star count is unavailable.
- Aggregate repositories whose status is not `succeeded` by status only; do not bucket them by stars.
- Cache the latest successful statistics so the dashboard remains usable if a refresh fails.

## Notes

- Query the Aurora PostgreSQL data source through ECS Exec and the container `$DATABASE_URL`.
- Do not hardcode database credentials.
