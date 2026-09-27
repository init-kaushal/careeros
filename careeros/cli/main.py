import typer
from careeros.cli.init_cmd import init_cmd
from careeros.cli.onboard import onboard_cmd
from careeros.cli.portability import export_cmd, import_workspace_cmd
from careeros.cli.workspace_cmd import workspace_app
from careeros.cli.job_cmd import job_app
from careeros.cli.browse_cmd import browse_cmd
from careeros.cli.browser_cmd import browser_app
from careeros.cli.board_cmd import board_app
from careeros.cli.apply_cmd import apply_cmd
from careeros.cli.discover_and_apply_cmd import discover_and_apply_app
from careeros.cli.research_cmd import research_app
from careeros.cli.outreach_cmd import outreach_app, people_app
from careeros.cli.resume_cmd import resume_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("init")(init_cmd)
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")
app.add_typer(resume_app, name="resume")
app.add_typer(job_app, name="job")
app.command("browse")(browse_cmd)
app.add_typer(browser_app, name="browser")
app.add_typer(board_app, name="board")
app.command("apply")(apply_cmd)
app.add_typer(discover_and_apply_app, name="discover-and-apply")
app.add_typer(research_app, name="research")
app.add_typer(outreach_app, name="outreach")
app.add_typer(people_app, name="people")
app.command("export")(export_cmd)
app.command("import")(import_workspace_cmd)

if __name__ == "__main__":
    app()
