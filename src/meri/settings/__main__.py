import logging
from pathlib import Path

# Try rich_click for better CLI experience
# but fallback to click if not available
try:
    import rich_click as click
except ImportError:
    import click

from pydantic_yaml import to_yaml_str

from .settings import DEFAULT_CONFIG_PATH, Settings

logger = logging.getLogger(__name__)

@click.group()
@click.pass_context
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode.",
)
def cli(ctx: click.Context, debug: bool):
    """
    CLI for managing settings.
    """
    settings = Settings(debug=debug)

    if ctx.invoked_subcommand is None:
        # If no subcommand is provided, show the help message
        click.echo("No subcommand provided.")
        click.echo(ctx.get_help())
        ctx.exit()
    else:
        # Set the debug mode based on the command line argument
        if debug:
            settings.DEBUG = True
        logger.setLevel(logging.DEBUG if settings.DEBUG else settings.LOGGING_LEVEL)

    from .settings import settings_var
    settings_var.set(settings)


@cli.command()
def show():
    """
    Show the currently effective settings.
    """
    from .settings import settings
    click.echo(settings.model_dump_json(indent=2))


@cli.command()
@click.argument("filename", type=click.Path(path_type=Path, exists=False, dir_okay=False, writable=True), default=DEFAULT_CONFIG_PATH)
def generate(filename: Path):
    """
    Generate the settings file <FILENAME>.

    By default, it will be created either in the user config directory or in the directory defined by the
    $KLIKKIKURI_CONFIG_FILE environment variable.
    """
    from .settings import settings

    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.touch(exist_ok=True)

    yaml = to_yaml_str(settings)

    with filename.open("w", encoding="utf-8") as f:
        f.write(yaml)
    click.echo(f"Settings file generated at {filename}")


if __name__ == "__main__":
    cli()
