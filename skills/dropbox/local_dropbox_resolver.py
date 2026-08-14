"""
local_dropbox_resolver.py — resolve a Karol BOM (job + project + sub-BOM) to its
LOCAL synced parts-folder path WITHOUT the Dropbox API.

WHY THIS EXISTS
---------------
Karol's sub-BOM shared links are Dropbox "scl/fo/..." URLs owned by HIS personal
account. When a SAS app token (a different account) calls
get_shared_link_metadata, the API authenticates and returns the folder *name*,
but NO `path_lower` (Dropbox only returns an in-account path for folders in the
token's own namespace). resolve_to_local() in dropbox_api.py needs `path_lower`,
so it cannot map a cross-account link to a local folder.

BUT the folder is ALREADY synced locally under the standard convention:

    C:\\Users\\GilesHugo\\SAS Dropbox\\3. Projects\\{Project folder}\\…\\2.3. Order\\{N}.{BOM name}

so we don't need Dropbox to tell us where it is — the host already has it. This
resolver finds it from the job number + project name + sub-BOM name that the
button/tracker already carries.

Conventions verified from real manifests (bom_pipeline/jobs/*.json):
  * Langley Street 5.Button Stations ->
    …\\3. Projects\\Langley street\\Design\\2.3. Order\\5.Button Stations
    Note: Asana "Langley Street" → local "Langley street" (case differs).
  * St George's House 1.Long Leading Time ->
    …\\3. Projects\\St George's House West\\2.3. Order\\1.Long Leading Time
    Note: Asana "St George's House" → local "St George's House West" (name differs).
  * The `2.3. Order` folder may sit under a `Design` intermediate, or directly
    under the project folder. The resolver tries both.

Design (Option 3 in the advisory):
  1. try the Dropbox-API resolve_to_local first (works for same-account folders)
  2. on failure, local name-match by job + project + sub-BOM
  3. a small AMBIGUITY lookup table fixes known folder-name mismatches
  4. a bounded fuzzy-glob under 3. Projects handles case/spacing drift
  5. if no unique project folder is found, return None and let the caller flag
     it for manual resolution (never guess a path blindly).

Read-only by policy. Never writes anything.
"""
from __future__ import annotations

import re
import difflib
from pathlib import Path

# Local root of the synced SAS Dropbox (matches dropbox_api.py default).
SAS_DROPBOX_ROOT = Path(
    __import__("os").environ.get("SAS_DROPBOX_ROOT", r"C:\Users\GilesHugo\SAS Dropbox"))
PROJECTS_ROOT = SAS_DROPBOX_ROOT / "3. Projects"

# --- Ambiguity lookup: Asana project name (or job) -> local 3. Projects folder.
# Keys are matched case-insensitively on the Asana project segment; values are the
# exact local folder name under 3. Projects. Add entries as new mismatches surface.
# (Do NOT list every project here — only the ones where the Asana name does NOT
# round-trip to the local folder name.)
PROJECT_NAME_OVERRIDES: dict[str, str] = {
    "st george's house": "St George's House West",
    "st georges house":  "St George's House West",
    "st george's house west": "St George's House West",  # already-exact, no-op
}
# Job number -> local folder override, for projects whose Asana name genuinely
# differs from the local folder and no sensible name match exists.
JOB_OVERRIDES: dict[str, str] = {}


def _norm(s: str) -> str:
    """Normalise a name for comparison: lowercase, collapse whitespace, drop
    some punctuation. Keep enough to distinguish real folder names."""
    s = (s or "").lower()
    s = re.sub(r"[\s_\-\.]+", " ", s).strip()
    return s


def _fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _project_folder_candidates(project_folder_name: str | None) -> list[Path]:
    """Return candidate project-folder Paths under 3. Projects, best-first."""
    if not PROJECTS_ROOT.is_dir():
        return []
    try:
        folders = [p for p in PROJECTS_ROOT.iterdir() if p.is_dir()]
    except Exception:
        return []
    if not folders:
        return []

    # 1) exact-name override hit
    if project_folder_name:
        ov = PROJECT_NAME_OVERRIDES.get(_norm(project_folder_name))
        if ov:
            exact = PROJECTS_ROOT / ov
            if exact.is_dir():
                return [exact]

    # 2) fuzzy-match the Asana project name against local folder names
    ranked: list[tuple[float, Path]] = []
    if project_folder_name:
        for p in folders:
            r = _fuzzy_ratio(project_folder_name, p.name)
            if r >= 0.72:          # tolerate case/spacing/word-order drift
                ranked.append((r, p))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if ranked:
            return [p for _, p in ranked]

    # 3) nothing matched by name; fall back to ALL folders (caller disambiguates)
    return folders


def _find_order_subfolder(project_folder: Path, bom_name: str | None) -> Path | None:
    """Inside a project folder, locate '{...}/2.3. Order/<bom_name>'. Tries
    both 'Design/2.3. Order' (most common) and bare '2.3. Order'. Returns the
    parts-folder Path or None. The sub-BOM name may carry a leading '<N>.' or
    an alternate suffix ('.rev1'), so match on the name's core slug."""
    if not bom_name:
        return None
    core = _norm(bom_name).replace(" ", "")
    # sanity: the folder we're looking for contains the sub-BOM's distinctive token
    tokens = [t for t in re.split(r"[^a-z0-9]+", _norm(bom_name)) if t]
    if not tokens:
        return None

    # locator folders to try, deepest-first for a bespoke match
    for base in (project_folder / "Design" / "2.3. Order",
                 project_folder / "2.3. Order",
                 project_folder / "Design" / "#Orders",
                 project_folder / "#Orders"):
        if not base.is_dir():
            continue
        try:
            kids = [p for p in base.iterdir() if p.is_dir()]
        except Exception:
            continue
        for k in kids:
            kc = _norm(k.name).replace(" ", "")
            # exact slug match OR prefix match (bom '1.Long Leading Time' will
            # commonly match a folder '1.Long Leading Time')
            if kc == core or kc.startswith(core) or core.startswith(kc):
                return k
    return None


def resolve_by_name(job: str | None = None,
                    project_name: str | None = None,
                    bom_name: str | None = None) -> Path | None:
    """
    Resolve job + project + sub-BOM to a local parts-folder path under
    SAS Dropbox\\3. Projects. Returns Path or None (no unique/unambiguous match).
    """
    # project folder name guess: strip leading '<job> - ' from project_name if
    # present (e.g. '1595 - St George's House' -> 'St George's House')
    proj_guess = project_name or ""
    if job and proj_guess.lower().startswith(f"{job} ".lower()) or (
            proj_guess and re.match(rf"^{re.escape(str(job))}\s*[-–]", proj_guess)):
        proj_guess = re.sub(rf"^{re.escape(str(job))}\s*[-–]\s*", "", proj_guess, count=1)

    # job-number override beats everything
    if job:
        jo = JOB_OVERRIDES.get(str(job))
        if jo:
            direct = PROJECTS_ROOT / jo
            if direct.is_dir():
                return _find_order_subfolder(direct, bom_name)

    cands = _project_folder_candidates(proj_guess or None)
    # prefer a candidate whose folder name contains the proj_guess core
    if proj_guess:
        core = _norm(proj_guess).replace(" ", "")
        scored = []
        for p in cands:
            pc = _norm(p.name).replace(" ", "")
            r = _fuzzy_ratio(proj_guess, p.name)
            contain = core in pc or pc in core
            scored.append((r + (0.2 if contain else 0.0), p))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= 0.72:
            return _find_order_subfolder(scored[0][1], bom_name)

    # multi-candidate ambiguity: try each in turn, return first that has the
    # sub-BOM folder; if more than one would match, that's ambiguous -> None
    matched = [_find_order_subfolder(p, bom_name) for p in cands]
    found = [m for m in matched if m]
    if len(found) == 1:
        return found[0]
    return None


def try_api_then_local(dropbox_url: str,
                       job: str | None = None,
                       project_name: str | None = None,
                       bom_name: str | None = None,
                       resolve_to_local=None) -> Path | None:
    """
    Composite resolver for the dispatch path:
      1. try the Dropbox-API resolve_to_local (same-account folders get the
         exact path_lower-based path)
      2. fall back to resolve_by_name (Karol cross-account scl/fo links)
    `resolve_to_local` is injected so this module stays import-safe without the
    dropbox_api dependency (the caller passes the dropbox_api.resolve_to_local).
    """
    # 1) API attempt — only if we have a resolver and a URL
    if dropbox_url and resolve_to_local is not None:
        try:
            p = resolve_to_local(dropbox_url)
            if p and Path(p).exists():
                return Path(p)
        except Exception:
            pass  # cross-account / missing token — fall through to local
    # 2) local name-match
    return resolve_by_name(job=job, project_name=project_name, bom_name=bom_name)


if __name__ == "__main__":
    # quick manual test: St George's (1595) sub-BOM
    print("St George's:", resolve_by_name("1595", "1595 - St George's House", "1.Long Leading Time"))
    print("Langley 5.ButtonStations:",
          resolve_by_name("1037", "1037 - Langley Street", "5.Button Stations"))
