# Start here: backend lead takeover

The active goal is a scientifically defensible RADARSAT-2 Tropical Forest change pipeline and a reliable five-minute dashboard demonstration. Human teammates own frontend. The lead owns acquisition, processing, API, worker coordination and validation.

Read `docs/TAKEOVER.md` for the ordered goal ledger, current evidence, exact source heads, worker ownership, blockers and next actions. Read `docs/PROGRESS.md` for milestone status. Update both as work progresses.

Durable remote: https://github.com/758gianni/mission-accepted-2026.git

Active lead branch: `forestwatch/offline-recovery-20261003`. Current lead worktree: `/tmp/forestwatch-recovery`. Temporary paths can disappear after an environment restart; remote branches and commit SHAs are authoritative.

The user checkout is `/home/overlord/hackathon/mission-accepted-2026`, on main with teammates' work. Preserve its untracked `.agents/` and `data/`. Do not reset, overwrite, or force-push it to recover the lead branch. A copy of this pointer in that checkout is deliberately untracked so the next LLM can find the active work without changing main.

If the lead worktree is missing, inspect existing worktrees and local changes first, fetch the published lead branch, then create a separate recovery worktree at a new path. Do not assume older temporary directories exist or that functions.store survived the restart.

SwarmForge team: `forest-change-20261002`. Read `.agents/skills/using-swarmforge/SKILL.md`; query existing workers before delegating. One source owner per scope. Independently review exact heads before merging and run combined checks. Preserve artifacts/source before VM destruction.

Only the user performs real EODMS credential actions. Provide an exact safe interactive command when the wrapper is ready; never request or store their password. No real scene bytes, validated real change region or completed real-data demo existed at the latest checkpoint. Consult the living ledger for changes to that state.
