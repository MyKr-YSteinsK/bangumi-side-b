# Safe data reset and audit

The application intentionally has no `reset --all` or `purge` command. It
never automatically deletes a user's SQLite database, downloaded covers, or
static output.

If an older workspace or generated site must be set aside before importing the
2026-04 release, first close processes that could use these files. Choose an
empty directory **outside this repository** for the backup root, replace the
placeholder below, and move files rather than deleting them.

```powershell
Set-Location <project-root>

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = "<outside-repository-backup-root>"
$workspaceBackup = Join-Path $backupRoot "workspace-before-2026-04-$stamp"
$distBackup = Join-Path $backupRoot "dist-before-2026-04-$stamp"

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

if ((Test-Path .\workspace) -and (Test-Path $workspaceBackup)) {
    throw "Workspace backup destination already exists. Choose a new timestamp."
}
if (Test-Path .\workspace) {
    Move-Item -LiteralPath .\workspace -Destination $workspaceBackup
}

if ((Test-Path .\dist) -and (Test-Path $distBackup)) {
    throw "Build backup destination already exists. Choose a new timestamp."
}
if (Test-Path .\dist) {
    Move-Item -LiteralPath .\dist -Destination $distBackup
}

git status --short
```

The backups remain recoverable outside the repository. Do not commit them.
After the move, initialise the new release data with:

```powershell
bgmb sync --progress plain 2026 4
bgmb audit
bgmb build --all
```

`sync` creates the workspace, SQLite schema, reports, and subject-cover cache
as needed. It does not create or download character media. `audit` is strictly
read-only: it neither migrates, repairs, nor deletes data. It checks SQLite
integrity and foreign keys; the 2026-04 new Japan-TV scope; country evidence;
absence of continuation, role, person, and character-media data; safe cover
paths; blacklist residue; and Pages build-marker status.

An audit failure returns a non-zero exit code and compact counts/reasons. Fix
the data manually or restore the moved backup, then run the audit again.
