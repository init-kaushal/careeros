def test_package_imports():
    import careeros
    assert careeros.__version__ == "0.1.0"

def test_cli_help():
    from typer.testing import CliRunner
    from careeros.cli.main import app
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "careeros" in result.output.lower()
