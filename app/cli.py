"""Command-line helpers, e.g.

    flask --app app ingest old_calendar_app/mysite/emails/*.txt
    flask --app app ingest --no-notify some_email.txt
"""
import uuid
from pathlib import Path
from unittest import mock

import click

from . import ingest


def register(app):
    @app.cli.command("ingest")
    @click.argument("files", nargs=-1, type=click.Path(exists=True))
    @click.option("--notify/--no-notify", default=False,
                  help="Actually email RAs (default: just record on the calendar).")
    def ingest_cmd(files, notify):
        """Feed saved email text files through the same pipeline as the webhook."""
        for f in sorted(files):
            text = Path(f).read_text()
            fake_id = f"cli-{uuid.uuid4()}"
            if notify:
                rec = ingest.process_email(fake_id, "cli", Path(f).name, text)
            else:
                with mock.patch.object(ingest, "send_email"):
                    rec = ingest.process_email(fake_id, "cli", Path(f).name, text)
            click.echo(f"{f}: {rec.outcome} {rec.detail}")
