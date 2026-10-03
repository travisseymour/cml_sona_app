# CML Sona Scheduler

Receives Sona sign-up and cancellation emails, emails the research assistant tagged in each one (for example `RA-TLS`), and shows every session on a shared calendar with one color per RA.

```
Sona ──> lab Gmail ──(filter: auto-forward)──> cml@mail.example.org
                                                   │  Resend Inbound (MX)
                                                   ▼
                              Resend webhook  POST /webhooks/resend
                                                   │
                         ┌─────────────────────────┴─────────────────────┐
                         ▼                                               ▼
              Sona notice: record the session,              anything else (e.g. Gmail's
              email each tagged RA via Resend               forwarding confirmation):
                                                            forward to ADMIN_EMAIL
                         ▼
              Calendar at /  (FullCalendar; month, week, day and list views)
```

## How it works

* **RA tags.** Any `RA-XXX` in the email body is matched against `ras.json`. Put the tag in the Sona timeslot's *location* field, for example `SS2 443C (RA-TLS)`, and it shows up in every notice. A notice with several tags notifies all of them. The calendar colors the session by the first tag.
* **Cancellations** mark the matching sign-up (same study, participant and start time) as cancelled. On the calendar it then appears in a lighter version of the RA's color, struck through with a thick red line. If the sign-up came in before this app existed, a cancelled entry is created anyway.
* **Problems go to ADMIN_EMAIL.** This covers missing tags, initials that aren't in `ras.json`, failed sends, and any email that isn't a Sona notice.
* Duplicate webhooks and emails forwarded twice are ignored.

## RA list: the `RA_CONFIG_JSON` variable

The RA roster contains email addresses, so it is **not in the repo**. On Railway it is stored in a service variable called `RA_CONFIG_JSON`:

```json
{
  "TLS": {"name": "Taylor S.", "email": "tls@ucsc.edu", "color": "#1f77b4"},
  "MCP": {"name": "Morgan P.", "email": "mcp@ucsc.edu"},
  "KLM": {"name": "Kim M.",    "email": "klm@ucsc.edu", "active": false}
}
```

* `color` is optional; RAs without one get a color from a built-in palette.
* `"active": false` keeps a former RA's color on old sessions but hides them from the legend and stops emailing them.
* The short form `"TLS": "tls@ucsc.edu"` also works.
* To add or change an RA, edit the variable in Railway (Variables tab → Raw Editor accepts multi-line JSON) and deploy the staged change. No code push is needed.
* The roster is checked at startup. If the variable is missing on Railway, isn't valid JSON, or has an entry without an email, the new deploy fails its health check and Railway keeps the previous deploy running. The deploy log names the problem without printing the addresses.

For local development, put the same JSON in a `ras.json` file in the project root (it's git-ignored), or set `RA_CONFIG_JSON` in your shell. If neither is present, the app falls back to `ras.example.json`, which contains placeholder addresses only.

## One-time setup

1. **Resend: receiving.** In Resend, open Domains → `mail.example.org` and enable *Receiving*. Then add the MX record Resend shows to the DNS for `mail.example.org`.
2. **Railway.** Create a new service from this GitHub repo. Add a **Postgres** database to the project and reference its `DATABASE_URL` in the service. Alternatively, attach a volume at `/data` and set `DATA_DIR=/data` to use SQLite. Without one of these, sessions are lost on every deploy. Then set the variables listed in `.env.example`: `RA_CONFIG_JSON`, `RESEND_API_KEY`, `MAIL_FROM`, `ADMIN_EMAIL`, `APP_URL`, `CALENDAR_PASSWORD` and `SECRET_KEY`.
3. **Custom domain (optional).** In Railway, go to Settings → Networking and add `cml.example.org`. Then add the CNAME record it gives you.
4. **Resend: webhook.** Under Webhooks, add `https://<your-app>/webhooks/resend` for the event `email.received`. Copy its signing secret into the Railway variable `RESEND_WEBHOOK_SECRET`. In production, webhooks are refused until this variable is set.
5. **Gmail (lab account).**
   1. Go to Settings → Forwarding and POP/IMAP → *Add a forwarding address* and enter `cml@mail.example.org`. Gmail sends a confirmation email to that address, and the app forwards it to `ADMIN_EMAIL`. Click the link in it, or enter its code.
   2. Create a filter: *From* `sona-systems.net`, *Subject* `Study Sign-Up OR Study Cancellation`. Choose *Forward it to* `cml@mail.example.org`.
6. Send yourself a test sign-up (or forward an old one) and watch the Railway logs.

## Local development

```bash
uv venv && uv pip install -r requirements.txt pytest
.venv/bin/pytest
.venv/bin/flask --app app ingest old_calendar_app/mysite/emails/*.txt   # load sample emails (no emails sent)
.venv/bin/flask --app app run                                          # http://localhost:5000
```

Without `RESEND_API_KEY`, outgoing emails are written to the log instead of sent. Without `CALENDAR_PASSWORD`, the calendar has no login.

## License

GNU General Public License v3.0. See `LICENSE`.
