import typer

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")

@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit."
    ),
):
    """CareerOS — your career, your data."""
    if version:
        print("careeros 0.1.0")
        raise typer.Exit()

if __name__ == "__main__":
    app()
