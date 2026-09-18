# Terminal break — ROOT CAUSE CORRECTED (2026-09-11)

## Symptom
Every `terminal()`, `write_file()`, `search_files()` call fails with:
  "Windows Subsystem for Linux has no installed distributions." + WSL_E_DEFAULT_DISTRO_NOT_FOUND
Even `pwd; echo test-ok` returns only the WSL stub message. `execute_code` is unaffected.

## The August note was WRONG (or incomplete) — corrected here
The 2026-08-24 diagnosis said: "C:\Program Files\Git\bin was absent from the user PATH".
That is NO LONGER true. Verified 2026-09-11: Git\bin IS present in the USER PATH (first entry).

Adding it to the user PATH is therefore NOT the fix, and explains why the fault RECURRED.

## Actual root cause: MACHINE PATH ordering
Windows composes the process PATH as:  MACHINE_PATH ; USER_PATH
(Machine entries are searched BEFORE user entries.)

MACHINE PATH (verified):
  c:\Users\GilesHugo\...\cursor\...\bin ; C:\Windows\system32 ; C:\Windows ; ...

C:\Windows\system32 contains bash.exe — the WSL launcher stub.
Hermes' terminal backend (terminal.backend: local) resolves the shell by PATH lookup of `bash`.
Because system32 appears in MACHINE PATH and is searched first, it ALWAYS shadows
git-bash's C:\Program Files\Git\bin\bash.exe, no matter what the user PATH contains.

Evidence:
  shutil.which("bash")                     -> C:\Windows\system32\bash.EXE   (WRONG)
  C:\Windows\System32\bash.exe           exists  (the WSL stub)
  C:\Program Files\Git\bin\bash.exe     exists  (the real MSYS bash)
  With Git\bin prepended FIRST:  (Get-Command bash).Source -> C:\Program Files\Git\bin\bash.exe (CORRECT)

## Why the earlier "user PATH" fix failed
User PATH is appended AFTER machine PATH, so it can never outrank system32.
Any fix that only edits the USER path is ineffective for this specific collision.

## The fix (HITL — machine-level, needs Giles approval)
Prepend C:\Program Files\Git\bin to the MACHINE PATH (system-wide), OR — if we want to
avoid a system-wide change — install a WSL distro so system32\bash.exe stops erroring
(that changes behaviour for everything relying on the stub, so the PATH fix is preferable).

Command shape (admin privileges required):
  setx /M PATH "C:\Program Files\Git\bin;%PATH%"     # setx truncates at 1024 chars - RISKY here
  # Safer: edit the registry value directly from an ELEVATED shell:
  #   HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment  -> Path
  # Current machine PATH length: ~560 chars (fits, but verify before/after)

Then a FULL restart of whatever launches Hermes (the gateway process) from a NEW shell,
since running processes keep the PATH they started with.

## Backup
Previous MACHINE + USER path values saved to:
  SES_Workspace/memory/terminal_path_backup_2026-09-11.json

## Workaround (used this session, no admin needed)
Use execute_code + subprocess with FULL EXE PATHS:
  SSH  = r"C:\Windows\System32\OpenSSH\ssh.exe"
  BASH = r"C:\Program Files\Git\bin\bash.exe"
  OPENSSL = r"C:\Program Files\Git\usr\bin\openssl.exe"
  PS   = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
Feed multi-line remote scripts as BINARY stdin to `ssh vps bash -s` (str input leaks CRLF).

## Impact on deployments
Real: a broken terminal silently forces agents onto workarounds and can make
`write_file` fail, so files appear "not written" (seen twice today in subagents).
Deployments still succeeded because execute_code was used. Treat as HIGH for
developer/agent ergonomics, MEDIUM for production risk (production runs on the VPS).
