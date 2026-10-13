---
name: herdr-reload-all-pi-sessions
description: Reload every live Pi session through Herdr without interrupting active turns. Use when the user asks to broadcast `/reload`, refresh extensions or skills across all Pi panes, or explicitly invokes this skill.
---

# Reload all Pi sessions through Herdr

Use the bundled script; Herdr has no native broadcast command.

## Procedure

1. Preview the current plan:

   ```bash
   python3 scripts/reload_all_sessions.py
   ```

2. Treat an explicit request to reload all sessions or direct invocation of this skill as approval. Execute:

   ```bash
   python3 scripts/reload_all_sessions.py --execute
   ```

3. Report the printed totals and run directory.
   - `idle` and `done` sessions receive `/reload` immediately through `herdr agent prompt`.
   - `working` and `blocked` sessions get detached workers that wait for `idle` or `done`, then submit `/reload`.
   - `unknown` and non-Pi panes are skipped and reported.

4. Read `summary.json` in the run directory when detailed immediate results are needed. Deferred workers write `<pane>.result.json` and `<pane>.worker.log` there.

## Safety and completion

- Use `agent prompt`, not raw key injection.
- Keep concurrency bounded; preserve active turns rather than steering `/reload` into them.
- Do not wait synchronously for the current session: its detached worker reloads it after this turn settles.
- Completion means every immediately safe target accepted `/reload`, every busy target has a deferred worker, and every skipped or failed target is reported.
