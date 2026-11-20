# KQL Insider Risk Assistant

A local web app that helps security analysts investigate **insider risk alerts in Microsoft Sentinel**. Describe an alert in plain English and the assistant returns ready-to-run **KQL queries**, a plain-English explanation of each one, and a tailored **investigation checklist**, all powered by the Claude API.

It is built for SOC analysts, especially junior ones, who need to move quickly from "an alert fired" to "here is what to check next".

![KQL Insider Risk Assistant screenshot](docs/screenshot.png)

## Quick start

```bash
git clone https://github.com/Danemma0/KQL-Assistant.git
cd KQL-Assistant
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env, then add your ANTHROPIC_API_KEY
python app.py
```

Then open <http://localhost:5000>. You need Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/). See [Getting started](#getting-started) for details.

> **Disclaimer:** Generated queries should always be reviewed and validated in your own Sentinel environment before use. Table and field names can differ between workspaces. This tool does not connect to Sentinel and does not run queries.

---

## Features

### Investigation assistant (chat)
- Describe an alert or scenario, for example *"A user downloaded 1,200 files from SharePoint at 11pm and connected a USB drive shortly after."*
- Choose an **alert type** for extra context: General, DLP, UEBA, SIEM, EDR, CASB, Pre-Termination Risk, Mass File Download, Data Exfiltration, Unusual Access.
- Each response includes:
  1. Alert type identification
  2. Ready-to-run KQL queries using real Sentinel tables (`OfficeActivity`, `DeviceFileEvents`, `SigninLogs`, `CloudAppEvents`, `BehaviorAnalytics`, `DLPAll` and more)
  3. A plain-English explanation of what each query does and why
  4. A step-by-step checklist: what to look for next, escalation triggers and what to document
  5. High-risk indicator flags, such as mass downloads, after-hours access, USB use and pre-termination patterns
- The conversation keeps context, so you can ask follow-up questions. **New Investigation** resets it.
- Markdown renders properly, KQL blocks are syntax-highlighted, and each has a **Copy Query** button.

### Saved investigations
- Every investigation is saved automatically to a local SQLite database.
- The sidebar lists recent investigations with the alert type, timestamp and a preview.

### PDF export
- Export any saved investigation as a PDF report containing:
  - The alert type and original scenario
  - A **3-part analyst summary**: *what is happening*, *what likely already occurred* and *what to expect next*
  - The full investigation detail

### Query Library
- A set of built-in preset queries (USB activity, mass download, after-hours access) that paste straight into the chat without using any API tokens.
- Save your own queries with a name, **rename** them and **delete** them. They persist locally.

### KQL Editor
- A dedicated editor tab to write or paste queries.
- **Live structural validation** as you type: unbalanced brackets and quotes, a leading pipe, empty `where` clauses, common operator typos and more.
- **Review with Claude** for a deeper check of syntax, inefficiencies, a suggested fix and a plain-English explanation.
- Save the query to your library, or send it to the chat for further work.

### UI
- Dark and light themes (the choice is remembered).
- Responsive layout that fits the viewport with an internally scrolling chat.

---

## Use cases

- Triage of Microsoft Purview **DLP**, **UEBA**, **EDR** and **CASB** alerts
- Pre-termination or resignation data-theft investigations
- Mass file download and exfiltration checks (USB, email, cloud upload)
- After-hours or out-of-scope access reviews
- Building a personal library of proven KQL queries
- Reviewing and sanity-checking KQL before running it in production
- Training junior analysts, because explanations and checklists are included
- Producing a concise PDF write-up for case files or handover

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| AI | Anthropic Claude API |
| Storage | SQLite (local file) |
| PDF | ReportLab |
| Frontend | Vanilla HTML/CSS/JS, highlight.js, marked |

---

## Getting started

### Prerequisites
- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
git clone https://github.com/Danemma0/KQL-Assistant.git
cd KQL-Assistant

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt

# Add your API key
cp .env.example .env      # Windows: copy .env.example .env
# then edit .env and set ANTHROPIC_API_KEY
```

### Run

```bash
python app.py
```

Open <http://localhost:5000>. The SQLite database (`kql_assistant.db`) is created automatically on first run.

---

## Project structure

```
.
├── app.py              # Flask routes: chat, history, PDF export, library, query review
├── database.py         # SQLite schema and migrations
├── system_prompt.py    # System prompt: KQL and insider-risk expertise
├── requirements.txt
├── .env.example        # Template for your API key
├── templates/
│   └── index.html
└── static/
    ├── script.js
    └── style.css
```

### API routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Web UI |
| POST | `/query` | Send a scenario, get queries and a checklist |
| GET | `/history` | Recent saved investigations |
| GET | `/export/<id>` | Download an investigation as PDF |
| GET / POST | `/library` | List or save library queries |
| PATCH / DELETE | `/library/<id>` | Rename, update or delete a library query |
| POST | `/review_query` | Claude review of a KQL query |
| POST | `/reset` | Clear the conversation |

---

## Security and privacy notes

- Your API key lives only in `.env`, which is git-ignored. Never commit it.
- Investigation history is stored **locally** in `kql_assistant.db` (git-ignored). Prompts and responses are sent to the Claude API, so **do not enter real personal data, usernames or confidential case details** unless your organisation's policies allow it. Use placeholders such as `username@domain.com`.
- This is a local development tool. Flask's debug mode is on by default; do not expose it to a network or the internet without hardening it (disable debug, add authentication, use a production server).

## Roadmap ideas

- Persistent chat sessions across restarts
- Export the query library to JSON/CSV
- Additional alert-type playbooks
- Optional direct Sentinel / Log Analytics integration

## Licence

Released under the [MIT License](LICENSE).
