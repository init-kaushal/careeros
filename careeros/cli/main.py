import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.portability import export_cmd, import_workspace_cmd
from careeros.cli.workspace_cmd import workspace_app
from careeros.cli.job_cmd import job_app
from careeros.cli.browse_cmd import browse_app
from careeros.cli.apply_cmd import apply_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")
app.add_typer(job_app, name="job")
app.add_typer(browse_app, name="browse")
app.add_typer(apply_app, name="apply")
app.command("export")(export_cmd)
app.command("import")(import_workspace_cmd)

if __name__ == "__main__":
    app()
