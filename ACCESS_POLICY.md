# ACCESS_POLICY.md - Agent File System Access

This document governs the agent's access to the local file system, in accordance with the "Least-Authority" principle in `Safety_first.md`.

## 1. Knowledge Ingestion Zones (Read-Only)

The agent has standing permission to **read** all files within the following directories for the purpose of learning, summarization, and knowledge base creation. The agent will not write to these locations without explicit, one-time confirmation.

- `C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\`
- `C:\Users\GilesHugo\projects\`
- `C:\Users\GilesHugo\notes\`

## 2. Agent Database Zone (Read/Write — sql_db skill only)

The local SQLite database and its audit log are owned by the agent ecosystem. All agents may read/write within the bounds defined in `workspace/skills/sql_db/SKILL.md`.

- `C:\Users\GilesHugo\.openclaw\workspace\db\`

Access is governed by the per-agent table policy in the sql_db SKILL.md. All writes are automatically audit-logged to `workspace/db/audit.log`.

## 3. Project Execution Zones (Read/Write with Confirmation)

These are active development directories. The agent can read files freely but requires **explicit confirmation** for any `write` or `edit` operations. This aligns with the `Operational (WRITE/MOD)` gate in `Safety_first.md`.

- `C:\Users\GilesHugo\projects\project-phoenix\`
- `C:\Users\GilesHugo\projects\project-pegasus\`

## 4. Restricted Zones

The agent will not attempt to access any paths outside of the zones defined above unless given an explicit, one-time instruction to do so. System directories, user profile folders (e.g., `AppData`), and other sensitive locations are off-limits by default.