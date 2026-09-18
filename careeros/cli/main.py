import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.workspace_cmd import workspace_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")

if __name__ == "__main__":
    app()
