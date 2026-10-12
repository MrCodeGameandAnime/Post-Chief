# Working on Post Chief

Read [docs/USAGE.md](docs/USAGE.md) before changing the project. It is the current
reference for setup, architecture, operations, providers, and agent permissions.

- Keep application code and generated runtime artifacts under `root/`; the MCP
  adapter belongs in `root/mcp/`. Run application commands from `root/`.
- Keep agent notes and scratch files in the sibling support directory. 
  Preserve existing user changes and project boundaries.
- Never expose credentials or replace existing signing/encryption keys casually.
- Preserve organization isolation, scopes, autonomy rules, approvals, and audits.
  Agents must never approve their own actions.
- Dispatch or upload acceptance does not prove publication. Inspect the durable
  destination outcome; reconcile uncertain writes before retrying.
- Do not create live posts, edit native content, spend money, or write external
  repositories merely to test a change. Obtain specific user authorization.
- Do not add/run tests, commit/push, or delegate to other agents without explicit
  authorization. Use branches/worktrees only when requested or the workflow requires them.
- Keep current documentation in `docs/USAGE.md`; avoid duplicating it in new guides.
