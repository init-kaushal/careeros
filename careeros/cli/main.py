import typer

from careeros.cli.approve import approve_cmd
from careeros.cli.archive import archive_cmd
from careeros.cli.doctor import doctor_cmd
from careeros.cli.init_cmd import init_cmd
from careeros.cli.ledger import ledger_app
from careeros.cli.migrate import migrate_cmd
from careeros.cli.status import status_cmd
from careeros.cli.transition import transition_cmd
from careeros.cli.upgrade import upgrade_cmd
from careeros.cli.validate import validate_cmd

app = typer.Typer(name="careeros", help="CareerOS — scaffold and maintain your job-search workspace.")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


app.command("init")(init_cmd)
app.command("doctor")(doctor_cmd)
app.command("status")(status_cmd)
app.command("validate")(validate_cmd)
app.command("transition")(transition_cmd)
app.command("approve")(approve_cmd)
app.command("archive")(archive_cmd)
app.command("migrate")(migrate_cmd)
app.command("upgrade")(upgrade_cmd)
app.add_typer(ledger_app, name="ledger")

if __name__ == "__main__":
    app()
