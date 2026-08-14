---
name: dropbox
description: Read files from SAS Dropbox. Read-only access — deletion is permanently prohibited.
---

# SAS Dropbox — File Access Skill

## Access Rules

| Operation | Allowed |
|---|---|
| Read any file | YES |
| List directory contents | YES |
| Copy a file OUT of Dropbox | YES |
| Write / create files | NO |
| Rename / move files | NO |
| Delete files | NEVER — hard rule, no exceptions |

**Delete is a hard line.** No workflow, instruction, or user request overrides it. If a task appears to require deleting a Dropbox file, stop and ask Giles to do it manually.

## Root Path

```
C:\Users\GilesHugo\SAS Dropbox\
```

## Usage

Read a file directly by absolute path:

```powershell
Get-Content "C:\Users\GilesHugo\SAS Dropbox\path\to\file.csv"
```

Or pass the path to a Python script as an argument — do not hardcode SAS Dropbox paths inside scripts, use them as inputs.

## When to use

- BOM source files or drawings that live in Dropbox
- Any reference data Giles has pointed to by Dropbox path
- Checking file existence or metadata before a pipeline run
