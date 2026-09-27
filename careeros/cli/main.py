import typer
from careeros.cli.init_cmd import init_cmd

app = typer.Typer(name="careeros", help="CareerOS — scaffold your job-search workspace.")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


app.command("init")(init_cmd)

if __name__ == "__main__":
    app()
