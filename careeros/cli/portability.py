import zipfile
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import print as rprint

from careeros.config import GlobalConfig
from careeros.runtime.factory import WorkspaceNotConfigured, resolve_storage
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace

_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500MB — generous for a personal workspace export
_MAX_COMPRESSION_RATIO = 100  # a legitimate JSON/text workspace won't compress much beyond this


def export_cmd(
    output: str | None = typer.Option(None, "--output", "-o", help="Output zip path"),
    workspace: str | None = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        storage = resolve_storage(workspace)
    except WorkspaceNotConfigured:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    ws_root = Path(storage.resolve("."))

    if not output:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output = str(Path.cwd() / f"{ws_root.name}-{date}.zip")

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in ws_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(ws_root)
            # skip internal migration receipts but keep .careeros dir structure
            if ".careeros/migrations" in str(rel) and str(rel) != ".careeros/migrations/.keep":
                continue
            zf.write(file_path, rel)

    rprint(f"[green]Exported workspace to {output}[/green]")


def import_workspace_cmd(
    source: str = typer.Argument(..., help="Path to workspace zip"),
    dest: str = typer.Option(..., "--dest", "-d", help="Destination directory (must not exist)"),
) -> None:
    source_path = Path(source).expanduser()
    if not source_path.exists():
        rprint(f"[red]File not found: {source}[/red]")
        raise typer.Exit(1)

    dest_path = Path(dest).expanduser()
    if dest_path.exists():
        rprint(f"[red]Destination already exists: {dest_path}[/red]")
        raise typer.Exit(1)

    dest_path.mkdir(parents=True)
    with zipfile.ZipFile(source_path, "r") as zf:
        total_uncompressed = 0
        for info in zf.infolist():
            if info.filename.startswith('/') or '..' in info.filename.split('/'):
                rprint(f"[red]Unsafe zip entry: {info.filename}[/red]")
                raise typer.Exit(1)
            total_uncompressed += info.file_size
            if info.compress_size > 0 and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
                rprint(f"[red]Zip entry '{info.filename}' has a suspicious compression ratio.[/red]")
                raise typer.Exit(1)
        if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
            rprint("[red]Zip would extract to more than 500MB — refusing.[/red]")
            raise typer.Exit(1)
        zf.extractall(dest_path)

    try:
        storage = LocalFilesystemStorage(str(dest_path))
        open_workspace(storage)
    except Exception as e:
        import shutil
        shutil.rmtree(dest_path, ignore_errors=True)
        rprint(f"[red]Restored workspace failed validation: {e}[/red]")
        raise typer.Exit(1)

    GlobalConfig(workspace_path=str(dest_path)).save()

    rprint(f"[green]Workspace imported to {dest_path}[/green]")
    rprint("Run [bold]careeros workspace validate[/bold] to confirm.")
