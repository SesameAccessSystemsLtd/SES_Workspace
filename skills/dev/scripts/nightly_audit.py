"""
nightly_audit.py — Hermes Nightly Codebase Audit
Runs each night to check health, consistency, and embedding opportunities.

Usage:
    python nightly_audit.py [--dry-run] [--no-email]

Output:
    workspace/memory/nightly/YYYY-MM-DD.md
    Email to Giles (via email_sender) if issues or opportunities found.
"""

import os
import sys
import re
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# ── Paths ──────────────────────────────────────────────────────────────────────
# OpenClaw retired 2026-07 — audit targets SES_Workspace.
SES_WORKSPACE = Path(__file__).resolve().parents[3]   # skills/dev/scripts -> SES_Workspace
WORKSPACE     = SES_WORKSPACE
AGENTS_DIR    = SES_WORKSPACE / "agents"
VENV_PYTHON   = SES_WORKSPACE / ".venv" / "Scripts" / "python.exe"
NOTIFY_SCRIPT = WORKSPACE / "skills" / "email_sender" / "scripts" / "notify.py"
OUTPUT_DIR    = SES_WORKSPACE / "memory" / "nightly"
SKILLS_DIR    = WORKSPACE / "skills"
PLANS_DIR     = WORKSPACE / "plans"

SKILLS_REGISTRY = WORKSPACE / "SKILLS_REGISTRY.md"
DATAMAP         = WORKSPACE / "DATAMAP.md"
SHARED_CONTEXT  = WORKSPACE / "SHARED_CONTEXT.md"
AGENTS_MD       = WORKSPACE / "AGENTS.md"

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW   = datetime.now()

# ── Helpers ────────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

def files_modified_since(root: Path, hours: int = 24) -> list[Path]:
    """Return files under root modified in the last N hours."""
    cutoff = NOW - timedelta(hours=hours)
    results = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                if mtime >= cutoff:
                    results.append(p)
            except Exception:
                pass
    return sorted(results, key=lambda p: p.stat().st_mtime, reverse=True)

def send_email(subject: str, body: str, dry_run: bool = False):
    cmd = [
        str(VENV_PYTHON),
        str(NOTIFY_SCRIPT),
        "--subject", subject,
        "--body", body,
        "--from-agent", "Hermes",
    ]
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)

# ── Checks ─────────────────────────────────────────────────────────────────────

def check_skills_registry() -> list[str]:
    """
    Compare skills/ directories against SKILLS_REGISTRY.md.
    Flag any skill directory that has no entry in the registry.
    """
    issues = []
    registry_text = read_file(SKILLS_REGISTRY)
    if not registry_text:
        return ["SKILLS_REGISTRY.md missing or empty — cannot validate skill entries."]

    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_name = skill_dir.name
        # Look for the skill name mentioned in the registry
        if skill_name.lower() not in registry_text.lower():
            issues.append(
                f"Skill directory `skills/{skill_name}/` has no entry in SKILLS_REGISTRY.md"
            )

    return issues

def check_agent_tools_vs_registry() -> list[str]:
    """
    For each agent under agents/, check they have a TOOLS.md.
    Note: main agent (Commander Claw) lives in workspace/ not agents/ — excluded here.
    """
    issues = []
    # Agents whose files live outside agents/ dir — skip them
    workspace_agents = {"main"}

    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name in workspace_agents:
            continue
        tools_file = agent_dir / "TOOLS.md"
        if not tools_file.exists():
            issues.append(f"Agent `{agent_dir.name}` has no TOOLS.md")

    return issues

def check_datamap_freshness() -> list[str]:
    """
    Parse DATAMAP.md for STALE or ERROR status entries.
    """
    issues = []
    datamap_text = read_file(DATAMAP)
    if not datamap_text:
        return ["DATAMAP.md missing or empty."]

    stale_count = datamap_text.upper().count("STALE")
    error_count = datamap_text.upper().count("ERROR")

    if stale_count > 0:
        issues.append(f"DATAMAP.md has {stale_count} STALE dataset(s) — check before next agent run.")
    if error_count > 0:
        issues.append(f"DATAMAP.md has {error_count} ERROR dataset(s) — requires attention.")

    return issues

def check_pending_plans() -> list[str]:
    """
    Flag plans that have been PENDING_APPROVAL for more than 7 days.
    """
    issues = []
    if not PLANS_DIR.exists():
        return []

    stale_cutoff = NOW - timedelta(days=7)
    for plan_file in PLANS_DIR.glob("PLAN-*.md"):
        text = read_file(plan_file)
        if "PENDING_APPROVAL" in text:
            try:
                mtime = datetime.fromtimestamp(plan_file.stat().st_mtime)
                if mtime < stale_cutoff:
                    issues.append(
                        f"Plan `{plan_file.name}` has been PENDING_APPROVAL for "
                        f"{(NOW - mtime).days} days — needs decision or cleanup."
                    )
            except Exception:
                pass

    return issues

def check_hardcoded_secrets() -> list[str]:
    """
    Scan Python scripts in workspace/skills/ for patterns that suggest hardcoded secrets.
    Heuristic only — not exhaustive.
    """
    issues = []
    secret_patterns = [
        r'api_key\s*=\s*["\'][a-zA-Z0-9_\-]{20,}["\']',
        r'token\s*=\s*["\'][a-zA-Z0-9_\-]{20,}["\']',
        r'password\s*=\s*["\'][^"\']{6,}["\']',
        r'sk-ant-api',
        r'AIza[0-9A-Za-z\-_]{35}',  # Google API key pattern
    ]
    compiled = [re.compile(p, re.IGNORECASE) for p in secret_patterns]

    this_script = Path(__file__).resolve()
    for py_file in SKILLS_DIR.rglob("*.py"):
        if py_file.resolve() == this_script:
            continue  # skip self — pattern strings would trigger false positives
        text = read_file(py_file)
        for pattern in compiled:
            if pattern.search(text):
                rel = py_file.relative_to(SES_WORKSPACE)
                issues.append(
                    f"Possible hardcoded secret in `{rel}` — review and move to env var."
                )
                break  # one flag per file is enough

    return issues

def find_embedding_opportunities() -> list[str]:
    """
    Look for repeated patterns across agent TOOLS.md files that could be shared.
    Simple heuristic: if the same skill script path appears in 3+ agents, it's worth noting.
    """
    opportunities = []
    skill_mentions: dict[str, list[str]] = {}

    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        tools_file = agent_dir / "TOOLS.md"
        if not tools_file.exists():
            continue
        text = read_file(tools_file)
        # Find workspace/skills/<name>/ references
        refs = re.findall(r"workspace/skills/(\w+)/", text)
        for ref in set(refs):
            skill_mentions.setdefault(ref, []).append(agent_dir.name)

    for skill, agents in skill_mentions.items():
        if len(agents) >= 3:
            opportunities.append(
                f"Skill `{skill}` referenced in {len(agents)} agents "
                f"({', '.join(agents)}) — confirm it's in SKILLS_REGISTRY.md with correct access policy."
            )

    return opportunities

def check_recently_modified() -> list[str]:
    """
    Summarise files modified in the last 24 hours for Giles's awareness.
    """
    modified = files_modified_since(WORKSPACE, hours=24)
    modified += files_modified_since(AGENTS_DIR, hours=24)

    if not modified:
        return []

    lines = [f"`{p.relative_to(SES_WORKSPACE)}`" for p in modified[:20]]
    if len(modified) > 20:
        lines.append(f"... and {len(modified) - 20} more")
    return lines

def check_orphan_scripts() -> list[str]:
    """
    Scan workspace root for .py files that should be in test_scripts/ or skills/.
    These are typically debug, exploratory, or temporary scripts cluttering the root.
    """
    issues = []
    
    # Whitelisted scripts that legitimately live at workspace root
    whitelist = {
        "asana_build_pack_allocator.py",  # Consider moving to skills/asana/scripts/
        "collate_ai_emails.py",           # Consider moving to skills/email_reader/scripts/
        "send_elastic_email.py",          # Consider moving to skills/elastic_email/scripts/
    }
    
    # Patterns of scripts that should go to test_scripts/
    test_prefixes = ("check_", "patch_", "find_", "clean", "test_", "delete_", "gen_", "get_", "list_", "set_", "trace_", "compare_")
    
    py_files = sorted([f for f in WORKSPACE.iterdir() if f.is_file() and f.suffix == ".py"])
    
    for py_file in py_files:
        if py_file.name in whitelist:
            continue
        
        # Check if this looks like a test/debug script
        if any(py_file.name.lower().startswith(prefix) for prefix in test_prefixes):
            issues.append(
                f"`{py_file.name}` — test/debug script at root, should move to `test_scripts/active/`"
            )
    
    return issues

def detect_redundancy() -> list[str]:
    """
    Find files with similar names (numbered variants, dated versions) that suggest
    redundancy or incomplete cleanup after experimentation.
    """
    issues = []
    
    # Scan workspace root and test_scripts for numbered/variant patterns
    scan_dirs = [WORKSPACE, WORKSPACE / "test_scripts"]
    variant_groups: dict[str, list[Path]] = {}
    
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        
        py_files = sorted([f for f in scan_dir.iterdir() if f.is_file() and f.suffix == ".py"])
        
        for py_file in py_files:
            # Strip common variant suffixes: _2, _3, _v2, 2, 3 (trailing digits), _old, _bak, _tmp, etc.
            # Pattern matches: _2, _v2, 2 (at end), _old, _bak, _tmp, _test, _debug
            base = re.sub(r'([_-]?v?\d+|_old|_bak|_tmp|_test|_debug)$', '', py_file.stem)
            if not base:  # Skip if entire name is just numbers
                continue
            variant_groups.setdefault(base, []).append(py_file)
    
    # Report groups with 2+ variants
    for base, variants in variant_groups.items():
        if len(variants) > 1:
            rel_paths = [str(v.relative_to(SES_WORKSPACE)) for v in variants]
            issues.append(
                f"Redundant variants of `{base}`: {', '.join([v.name for v in variants])} — "
                f"document which is canonical and archive others to `test_scripts/archive/`"
            )
    
    return issues

def check_workspace_layout() -> list[str]:
    """
    Verify that workspace follows approved directory structure.
    """
    issues = []
    
    # Expected core directories (SES_Workspace layout)
    expected_dirs = {"agents", "data", "db", "lib", "memory", "plans", "queues", "skills"}

    actual_dirs = {d.name for d in WORKSPACE.iterdir() if d.is_dir()}

    for exp_dir in expected_dirs:
        if exp_dir not in actual_dirs:
            issues.append(f"Missing expected directory: `{exp_dir}/`")

    # Check for unexpected directories that might indicate clutter
    known_extra = {"_copilot", "golden_index", "graphify-out", "personal",
                   "reference", "run_history", "test_scripts", "dev_temp", ".venv",
                   "_drafts", "scripts", "views"}
    unexpected = actual_dirs - expected_dirs - known_extra
    for unexp_dir in sorted(unexpected):
        if not unexp_dir.startswith("1575_"):
            issues.append(f"Unexpected directory at workspace root: `{unexp_dir}/` — verify this belongs here")
    
    return issues

def check_test_scripts_organization() -> list[str]:
    """
    Verify test_scripts/ folder is properly organized with subdirs and documentation.
    """
    issues = []
    test_scripts_dir = WORKSPACE / "test_scripts"

    if not test_scripts_dir.exists():
        return []  # optional in SES_Workspace layout
    
    # Check for expected subdirectories
    expected_subdirs = {"active", "archive"}
    actual_subdirs = {d.name for d in test_scripts_dir.iterdir() if d.is_dir()}
    
    for exp_sub in expected_subdirs:
        if exp_sub not in actual_subdirs:
            issues.append(f"test_scripts/ missing subdirectory: `{exp_sub}/`")
    
    # Check for documentation
    readme = test_scripts_dir / "README.md"
    if not readme.exists():
        issues.append("test_scripts/ missing README.md — should document each test script")
    
    variants_md = test_scripts_dir / "VARIANTS.md"
    if not variants_md.exists():
        # Only flag if there are actually variants present
        py_files = list(test_scripts_dir.rglob("*.py"))
        if any(re.search(r'(_v?\d+|_old|_bak)\.py$', f.name) for f in py_files):
            issues.append("test_scripts/ has variant files but missing VARIANTS.md documentation")
    
    return issues

def check_stale_artifacts() -> list[str]:
    """
    Check for old/stale files in dev_temp/ and test_scripts/archive/ that need cleanup.
    """
    issues = []
    stale_threshold_days = 7
    archive_threshold_days = 180
    
    stale_cutoff = NOW - timedelta(days=stale_threshold_days)
    archive_cutoff = NOW - timedelta(days=archive_threshold_days)
    
    dev_temp_dir = WORKSPACE / "dev_temp"
    if dev_temp_dir.exists():
        old_files = []
        for f in dev_temp_dir.rglob("*"):
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < stale_cutoff:
                        old_files.append((f.name, (NOW - mtime).days))
                except Exception:
                    pass
        
        if old_files:
            count = len(old_files)
            oldest_days = max(d for _, d in old_files)
            issues.append(
                f"dev_temp/ has {count} file(s) unmodified for >{stale_threshold_days} days "
                f"(oldest: {oldest_days} days) — consider auto-cleanup of weekly WIP artifacts"
            )
    
    test_archive_dir = WORKSPACE / "test_scripts" / "archive"
    if test_archive_dir.exists():
        very_old_files = []
        for f in test_archive_dir.rglob("*"):
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < archive_cutoff:
                        very_old_files.append((f.name, (NOW - mtime).days))
                except Exception:
                    pass
        
        if very_old_files:
            count = len(very_old_files)
            issues.append(
                f"test_scripts/archive/ has {count} file(s) unmodified for >{archive_threshold_days} days — "
                f"review for permanent archival or safe deletion"
            )
    
    return issues

# ── Report Builder ─────────────────────────────────────────────────────────────

def build_report(
    registry_issues: list[str],
    tools_issues: list[str],
    datamap_issues: list[str],
    plan_issues: list[str],
    secret_issues: list[str],
    opportunities: list[str],
    modified_files: list[str],
    orphan_issues: list[str],
    redundancy_issues: list[str],
    layout_issues: list[str],
    test_org_issues: list[str],
    stale_issues: list[str],
    api_issues: list[str],
) -> tuple[str, str, bool]:
    """Returns (report_text, email_text, has_issues)."""

    total_issues = (
        len(registry_issues) + len(tools_issues) + len(datamap_issues) +
        len(plan_issues) + len(secret_issues) + len(orphan_issues) +
        len(redundancy_issues) + len(layout_issues) + len(test_org_issues) + len(stale_issues) + len(api_issues)
    )
    total_opps = len(opportunities)
    has_issues = total_issues > 0 or total_opps > 0

    lines = [
        f"# Hermes Nightly Audit — {TODAY}",
        "",
        f"**Issues:** {total_issues}  |  **Opportunities:** {total_opps}  |  "
        f"**Files changed (24h):** {len(modified_files)}",
        "",
        "## STRUCTURAL ASSESSMENT",
        "",
    ]

    def section(title: str, items: list[str], prefix: str = "-"):
        if not items:
            return [f"## {title}", "", "_None._", ""]
        out = [f"## {title}", ""]
        for item in items:
            out.append(f"{prefix} {item}")
        out.append("")
        return out

    # Structural checks first (higher priority)
    lines += section("⚠️  Workspace Layout", layout_issues)
    lines += section("📦 Orphan Scripts at Root", orphan_issues)
    lines += section("🔀 Redundant File Variants", redundancy_issues)
    lines += section("🗂️  Test Scripts Organization", test_org_issues)
    lines += section("🗑️  Stale Artifacts", stale_issues)
    lines += section("🔌 API Connection Health", api_issues)

    lines += ["", "## CODEBASE HEALTH", ""]

    # Health checks
    lines += section("Skills Registry Gaps", registry_issues)
    lines += section("Agent TOOLS.md Gaps", tools_issues)
    lines += section("Data Freshness (DATAMAP)", datamap_issues)
    lines += section("Stale Plans", plan_issues)
    lines += section("Possible Hardcoded Secrets", secret_issues)
    lines += section("Embedding Opportunities", opportunities)
    lines += section("Files Modified in Last 24h", modified_files)

    lines += [
        "---",
        f"_Generated by Hermes nightly_audit.py at {NOW.strftime('%H:%M')} on {TODAY}_",
    ]

    # --- EMAIL SUMMARY BUILDER ---
    # Creates a lightweight, actionable email instead of dumping the massive markdown
    email_lines = [
        f"Hermes Nightly Audit Summary — {TODAY}",
        "",
        f"Found {total_issues} issue(s) and {total_opps} opportunity(s).",
        "",
        "💡 RECOMMENDED ACTIONS:",
    ]
    
    if layout_issues: email_lines.append("- Fix Workspace Layout: " + layout_issues[0])
    if orphan_issues: email_lines.append(f"- Move {len(orphan_issues)} orphan test scripts to test_scripts/active/")
    if redundancy_issues: email_lines.append(f"- Consolidate {len(redundancy_issues)} families of redundant scripts.")
    if secret_issues: email_lines.append("- ⚠️ URGENT: Check for hardcoded secrets. Review report.")
    if datamap_issues: email_lines.append("- Check DATAMAP.md: Stale or Error datasets detected.")
    if stale_issues: email_lines.append(f"- Clean up {len(stale_issues)} stale artifacts in dev_temp/ or test_scripts/archive/.")
    if api_issues: email_lines.append(f"- 🔌 Check API Connections: {len(api_issues)} potential config issue(s) detected.")
    
    email_lines += [
        "",
        f"For full details and file paths, review the complete report locally:",
        f"workspace/memory/nightly/{TODAY}.md",
        "",
        "— Hermes"
    ]

    return "\n".join(lines), "\n".join(email_lines), has_issues

# ── Main ───────────────────────────────────────────────────────────────────────

def check_api_health() -> list[str]:
    """Stub — API health checks not yet implemented."""
    return []


def check_dev_project_status() -> list[str]:
    """
    Check Giles Ai Dev Work project for tasks needing attention.
    Flags: overdue tasks, Not Started tasks with High/Critical priority, stalled In Progress items.
    """
    issues = []
    try:
        import requests, json as _json
        home = os.path.expanduser('~')
        token = None
        for env_path in ['.openclaw/.env', '.Hermes/.env']:
            fp = os.path.join(home, env_path)
            if os.path.exists(fp):
                with open(fp) as f:
                    for line in f:
                        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
                            token = line.split('=', 1)[1].strip().strip('"').strip("'")
                            break
                if token:
                    break
        if not token:
            return ["[Dev Project] Cannot check - ASANA_PERSONAL_ACCESS_TOKEN not found."]

        headers = {'Authorization': f'Bearer {token}'}
        project_gid = '1147424298774210'
        today = datetime.now().strftime('%Y-%m-%d')

        # Get all tasks
        r = requests.get('https://app.asana.com/api/1.0/tasks', headers=headers, params={
            'project': project_gid,
            'limit': 50,
            'opt_fields': 'name,completed,due_on,custom_fields'
        }, timeout=15)
        if r.status_code != 200:
            return [f"[Dev Project] API error {r.status_code} - cannot check project."]

        tasks = r.json()['data']
        if not tasks:
            return ["[Dev Project] No tasks found - project may need populating."]

        incomplete = [t for t in tasks if not t.get('completed')]
        overdue = [t for t in incomplete if t.get('due_on') and t['due_on'] < today]

        # Count by status
        status_counts = {'Not Started': 0, 'In Progress': 0, 'Blocked': 0, 'Done': 0, 'Cancelled': 0, 'Unset': 0}
        priority_high = 0
        blocked_tasks = []

        for t in incomplete:
            cfs = t.get('custom_fields', [])
            found_status = False
            found_priority = False
            for cf in cfs:
                dv = cf.get('display_value')
                if cf.get('gid') == '1216356799734180' and dv:  # Status
                    status_counts[dv] = status_counts.get(dv, 0) + 1
                    found_status = True
                    if dv == 'Blocked':
                        blocked_tasks.append(t['name'][:60])
                if cf.get('gid') == '1216356798129221' and dv in ('Critical', 'High'):  # Priority
                    priority_high += 1
                    found_priority = True
            if not found_status:
                status_counts['Unset'] += 1

        total_incomplete = len(incomplete)

        # Build report
        lines = []
        lines.append(f"Dev Project: {total_incomplete} open, {len(overdue)} overdue, {priority_high} Critical/High priority")
        lines.append(f"  Status breakdown: {status_counts}")
        if overdue:
            for t in overdue[:5]:
                lines.append(f"  Overdue: {t['name'][:60]} (due {t.get('due_on', '?')})")
        if blocked_tasks:
            for t in blocked_tasks[:3]:
                lines.append(f"  Blocked: {t}")
        if status_counts.get('In Progress', 0) == 0 and status_counts.get('Not Started', 0) > 0:
            lines.append(f"  Tip: {status_counts['Not Started']} tasks not started - consider picking one for next session")

        return lines if lines else ["[Dev Project] All tasks up to date - no flags."]
    except Exception as e:
        return [f"[Dev Project] Check failed: {e}"]
def main():
    parser = argparse.ArgumentParser(description="Hermes nightly codebase audit")
    parser.add_argument("--dry-run", action="store_true", help="Don't send email, don't write report file")
    parser.add_argument("--no-email", action="store_true", help="Write report but don't email")
    args = parser.parse_args()

    print(f"[Hermes Audit] Starting — {TODAY} {NOW.strftime('%H:%M')}")

    # Health checks
    registry_issues  = check_skills_registry()
    tools_issues     = check_agent_tools_vs_registry()
    datamap_issues   = check_datamap_freshness()
    plan_issues      = check_pending_plans()
    secret_issues    = check_hardcoded_secrets()
    opportunities    = find_embedding_opportunities()
    modified_files   = check_recently_modified()
    
    # STRUCTURAL CHECKS (new)
    orphan_issues    = check_orphan_scripts()
    redundancy_issues = detect_redundancy()
    layout_issues    = check_workspace_layout()
    test_org_issues  = check_test_scripts_organization()
    stale_issues     = check_stale_artifacts()
    api_issues       = check_api_health()
    dev_project_issues = check_dev_project_status()

    report, email_text, has_issues = build_report(
        registry_issues, tools_issues, datamap_issues,
        plan_issues, secret_issues, opportunities, modified_files,
        orphan_issues, redundancy_issues, layout_issues, test_org_issues, stale_issues, api_issues,
    )

    # Add dev project section to the report
    if dev_project_issues:
        report_lines = report.split('\n')
        report_lines.append('')
        report_lines.append('## DEV PROJECT STATUS')
        report_lines.append('')
        for line in dev_project_issues:
            report_lines.append(line)
        report_lines.append('')
        report = '\n'.join(report_lines)
        has_issues = True

    # Write report
    if not args.dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = OUTPUT_DIR / f"{TODAY}.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"[Hermes Audit] Report written to {report_path}")

        # Also save as HTML for Giles's local review
        html_path = OUTPUT_DIR / f"{TODAY}.html"
        html_title = f"Hermes Nightly Audit — {TODAY}"
        html_body = report.replace('\n', '<br>\n')
        html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 800px; margin: 2em auto; padding: 0 1em; line-height: 1.5; }}
h1 {{ color: #1a1a2e; border-bottom: 2px solid #e0e0e0; }}
h2 {{ color: #16213e; margin-top: 1.5em; }}
.issue {{ color: #e74c3c; }} .opportunity {{ color: #27ae60; }}
pre {{ background: #f5f5f5; padding: 0.5em; border-radius: 4px; overflow-x: auto; }}
</style></head><body>
{html_body}
</body></html>"""
        html_path.write_text(html_content, encoding="utf-8")
        print(f"[Hermes Audit] HTML report saved to {html_path}")
    else:
        print("[Hermes Audit] DRY RUN — report not written")
        print(report)

    # Email only if there's something to report
    if has_issues and not args.no_email:
        total_issues = (
            len(registry_issues) + len(tools_issues) + len(datamap_issues) +
            len(plan_issues) + len(secret_issues) + len(orphan_issues) +
            len(redundancy_issues) + len(layout_issues) + len(test_org_issues) + len(stale_issues) + len(api_issues)
        )
        total_opps = len(opportunities)
        subject = f"Nightly Audit Action Summary — {total_issues} issue(s) [{TODAY}]"
        ok, msg = send_email(subject, email_text, dry_run=args.dry_run)
        if ok:
            print(f"[Hermes Audit] Email sent: {subject}")
        else:
            print(f"[Hermes Audit] Email FAILED: {msg}", file=sys.stderr)
    elif not has_issues:
        print("[Hermes Audit] No issues or opportunities — no email sent.")

    # Exit 0 when the audit RAN successfully. Findings (incl. secret issues) are
    # surfaced via the report + email, not via a non-zero exit — otherwise the cron
    # job shows red every night. Real run failures still raise/exit non-zero above.
    if len(secret_issues) > 0:
        print(f"[Hermes Audit] NOTE: {len(secret_issues)} secret issue(s) flagged — see report/email.")
    print("[Hermes Audit] Done.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
