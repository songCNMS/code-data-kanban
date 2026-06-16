<!-- METADATA:STATUS=Completed,ASSIGNEE=intern_code_data_debug -->

# task003_compact_repo_stats_layout

## Goal

Optimize the kanban dashboard repository collection layout so the two repository collection sections do not occupy most of the viewport.

## Requirements

- Keep `Repository Collection` and `CEPHFS Repository Collection` visible near the search controls.
- Prevent the stats sections from being trapped inside the sticky header.
- Make the two sections compact and independently scrollable when their metric cells overflow.
- Preserve existing stats endpoints and refresh behavior.

## Notes

- The issue is primarily layout: the sticky `header` currently wraps both stats sections, causing them to remain pinned and consume screen height.

## PR

- https://github.com/songCNMS/code-data-kanban/pull/4

## Completion

- Completed and approved for merge on 2026-06-16.
