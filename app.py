from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
from anthropic import Anthropic  # type: ignore[import]
from database import init_db
from system_prompt import SYSTEM_PROMPT
from masking import mask, unmask, stream_unmask, count_masked
from dotenv import load_dotenv
import os
import re
import html
import sqlite3
import uuid
import json

def strip_markdown(text):
    text = re.sub(r'```[\s\S]*?```', '[KQL Query — see full investigation in app]', text)
    text = re.sub(r'`[^`]*`', '', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text)
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'^[\s\-:]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = html.escape(text.strip())
    return text

load_dotenv()
print("API Key loaded:", bool(os.getenv("ANTHROPIC_API_KEY")))

app = Flask(__name__)
init_db()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# In-memory conversation state (single-user local app)
# ---------------------------------------------------------------------------
# conversation_history holds the MASKED messages sent to / received from Claude.
# All DB writes store the UNMASKED (original) text.

_state = {
    'conversation_id': None,
    'conversation_history': [],  # masked messages for the API
    'mask_session': {},          # {v2t: {value: token}, cnt: {prefix: int}}
}


def _db():
    conn = sqlite3.connect("kql_assistant.db")
    conn.row_factory = sqlite3.Row
    return conn


def _save_mask_session(conv_id: str, sm: dict):
    conn = _db()
    conn.execute("""
        INSERT INTO mask_sessions (conversation_id, map_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(conversation_id) DO UPDATE SET
            map_json   = excluded.map_json,
            updated_at = excluded.updated_at
    """, (conv_id, json.dumps(sm)))
    conn.commit()
    conn.close()


def _load_mask_session(conv_id: str) -> dict:
    conn = _db()
    row = conn.execute(
        "SELECT map_json FROM mask_sessions WHERE conversation_id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    if row:
        return json.loads(row['map_json'])
    return {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/query", methods=["POST"])
def query():
    data        = request.get_json()
    user_msg    = data.get("message", "").strip()
    alert_type  = data.get("alert_type", "General")
    reset       = data.get("reset", False)
    conv_id_in  = data.get("conversation_id")  # client may send existing conv id
    masking_on  = data.get("masking", True)
    extra_terms = [t.strip() for t in data.get("extra_terms", []) if t.strip()]

    if not user_msg:
        return jsonify({"error": "No message provided"}), 400

    # --- Conversation management ---
    if reset or not _state['conversation_id']:
        _state['conversation_id'] = str(uuid.uuid4())
        _state['conversation_history'] = []
        _state['mask_session'] = {}
    elif conv_id_in and conv_id_in != _state['conversation_id']:
        # Client asked to continue a different saved conversation
        _state['conversation_id'] = conv_id_in
        _state['mask_session'] = _load_mask_session(conv_id_in)
        # Rebuild masked history from DB
        conn = _db()
        rows = conn.execute("""
            SELECT role, content FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
        """, (conv_id_in,)).fetchall()
        conn.close()
        _state['conversation_history'] = []
        for row in rows:
            masked_content = (
                mask(row['content'], _state['mask_session'], extra_terms)
                if masking_on else row['content']
            )
            _state['conversation_history'].append(
                {"role": row['role'], "content": masked_content}
            )

    conv_id = _state['conversation_id']
    sm = _state['mask_session']

    # --- Mask the outbound user message ---
    full_message = f"[Alert Type: {alert_type}]\n\n{user_msg}"
    masked_message = mask(full_message, sm, extra_terms) if masking_on else full_message

    _state['conversation_history'].append({"role": "user", "content": masked_message})

    # --- Determine title (first user message, truncated) ---
    is_first = len(_state['conversation_history']) == 1
    title = user_msg[:60] + ("…" if len(user_msg) > 60 else "")

    # --- Streaming SSE response ---
    def generate():
        full_response_chunks = []
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=_state['conversation_history'],
            ) as stream:
                masked_chunks = stream.text_stream

                for unmasked_chunk in stream_unmask(masked_chunks, sm):
                    full_response_chunks.append(unmasked_chunk)
                    payload = json.dumps({"token": unmasked_chunk})
                    yield f"data: {payload}\n\n"

                final_msg = stream.get_final_message()
                usage = final_msg.usage

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        full_response = "".join(full_response_chunks)

        # --- Persist to DB (unmasked) ---
        conn = _db()

        # investigations row
        conn.execute("""
            INSERT INTO investigations (alert_type, prompt, response, conversation_id, title)
            VALUES (?, ?, ?, ?, ?)
        """, (alert_type, user_msg, full_response, conv_id,
              title if is_first else None))

        # conversation_messages rows
        conn.execute("""
            INSERT INTO conversation_messages (conversation_id, role, content)
            VALUES (?, 'user', ?)
        """, (conv_id, user_msg))
        conn.execute("""
            INSERT INTO conversation_messages (conversation_id, role, content)
            VALUES (?, 'assistant', ?)
        """, (conv_id, full_response))

        conn.commit()
        conn.close()

        # Persist mask session
        if masking_on:
            _save_mask_session(conv_id, sm)

        # Update in-memory history with the unmasked assistant reply stored
        # as a masked version so future context stays consistent
        masked_response = mask(full_response, sm) if masking_on else full_response
        _state['conversation_history'].append(
            {"role": "assistant", "content": masked_response}
        )

        done_payload = json.dumps({
            "done": True,
            "conversation_id": conv_id,
            "masked_count": count_masked(sm) if masking_on else 0,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": getattr(usage, 'cache_creation_input_tokens', 0),
                "cache_read_input_tokens": getattr(usage, 'cache_read_input_tokens', 0),
            },
        })
        yield f"data: {done_payload}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/preview_mask", methods=["POST"])
def preview_mask():
    """Return what will be sent to Claude after masking, without committing tokens."""
    from masking import preview as mask_preview
    data        = request.get_json()
    user_msg    = data.get("message", "").strip()
    alert_type  = data.get("alert_type", "General")
    extra_terms = [t.strip() for t in data.get("extra_terms", []) if t.strip()]
    if not user_msg:
        return jsonify({"error": "No message"}), 400
    text = f"[Alert Type: {alert_type}]\n\n{user_msg}"
    result = mask_preview(text, _state['mask_session'], extra_terms)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------

@app.route("/history")
def history():
    q = request.args.get("q", "").strip()
    conn = _db()
    if q:
        rows = conn.execute("""
            SELECT conversation_id,
                   alert_type,
                   MIN(prompt)      AS first_prompt,
                   MAX(created_at)  AS last_activity,
                   COUNT(*)         AS message_count,
                   MIN(title)       AS title
            FROM investigations
            WHERE conversation_id IS NOT NULL
              AND (prompt LIKE ? OR title LIKE ? OR response LIKE ?)
            GROUP BY conversation_id
            ORDER BY last_activity DESC
            LIMIT 50
        """, (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = conn.execute("""
            SELECT conversation_id,
                   alert_type,
                   MIN(prompt)      AS first_prompt,
                   MAX(created_at)  AS last_activity,
                   COUNT(*)         AS message_count,
                   MIN(title)       AS title
            FROM investigations
            WHERE conversation_id IS NOT NULL
            GROUP BY conversation_id
            ORDER BY last_activity DESC
            LIMIT 50
        """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/conversation/<conv_id>")
def load_conversation(conv_id):
    """Return all messages for a conversation (for the sidebar restore)."""
    conn = _db()
    rows = conn.execute("""
        SELECT role, content, created_at
        FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    """, (conv_id,)).fetchall()
    meta = conn.execute("""
        SELECT alert_type, MIN(title) AS title
        FROM investigations
        WHERE conversation_id = ?
    """, (conv_id,)).fetchone()
    conn.close()
    return jsonify({
        "conversation_id": conv_id,
        "alert_type": meta['alert_type'] if meta else "General",
        "title": meta['title'] if meta else "",
        "messages": [dict(r) for r in rows],
    })


@app.route("/conversation/<conv_id>", methods=["PATCH"])
def rename_conversation(conv_id):
    data  = request.get_json()
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title required"}), 400
    conn = _db()
    conn.execute("""
        UPDATE investigations SET title = ?
        WHERE conversation_id = ? AND id = (
            SELECT MIN(id) FROM investigations WHERE conversation_id = ?
        )
    """, (title, conv_id, conv_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "renamed"})


@app.route("/conversation/<conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    conn = _db()
    conn.execute("DELETE FROM investigations WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM mask_sessions WHERE conversation_id = ?", (conv_id,))
    conn.commit()
    conn.close()
    if _state['conversation_id'] == conv_id:
        _state['conversation_id'] = None
        _state['conversation_history'] = []
        _state['mask_session'] = {}
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Legacy export
# ---------------------------------------------------------------------------

@app.route("/export/<conv_id>")
def export(conv_id):
    import tempfile

    # Accept either an integer id (legacy) or a conversation_id UUID
    conn = _db()
    if conv_id.isdigit():
        row = conn.execute("""
            SELECT alert_type, prompt, response
            FROM investigations WHERE id=?
        """, (int(conv_id),)).fetchone()
    else:
        row = conn.execute("""
            SELECT alert_type, MIN(prompt) AS prompt,
                   GROUP_CONCAT(response, '\n\n---\n\n') AS response
            FROM investigations WHERE conversation_id=?
        """, (conv_id,)).fetchone()

    if row is None:
        conn.close()
        return jsonify({"error": "Investigation not found"}), 404
    conn.close()

    alert_type = row['alert_type']
    prompt     = row['prompt']
    response   = row['response']

    try:
        summary_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system="""You are a senior SOC analyst writing a concise PDF investigation summary.
You must respond in exactly this format with no markdown, no bullet points, no special characters:

WHAT IS HAPPENING
Write 2-3 plain sentences describing what the alert indicates is occurring right now.

WHAT LIKELY ALREADY OCCURRED
Write 2-3 plain sentences describing what the threat actor or user has likely already done before this alert fired.

WHAT TO EXPECT NEXT
Write 2-3 plain sentences describing the most probable next actions or escalation path if this is not contained.

Use plain sentences only. No asterisks, no dashes, no headers with symbols.""",
            messages=[{
                "role": "user",
                "content": f"Alert Type: {alert_type}\n\nScenario: {prompt}\n\nInvestigation findings:\n{(response or '')[:2000]}"
            }]
        )
        analyst_summary = summary_response.content[0].text.strip()
    except Exception:
        analyst_summary = "Summary could not be generated."

    def extract_section(text, heading):
        pattern = rf'{heading}\s*\n(.*?)(?=\n[A-Z ]+\n|$)'
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    what_happening = extract_section(analyst_summary, "WHAT IS HAPPENING")
    what_occurred  = extract_section(analyst_summary, "WHAT LIKELY ALREADY OCCURRED")
    what_next      = extract_section(analyst_summary, "WHAT TO EXPECT NEXT")

    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    filename  = temp_file.name
    temp_file.close()

    doc    = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()

    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    section_style = ParagraphStyle(
        'SectionHeading', parent=styles['Heading3'],
        textColor=colors.HexColor('#1a3a5c'), spaceAfter=4, spaceBefore=10,
    )
    box_style = ParagraphStyle(
        'BoxText', parent=styles['BodyText'],
        backColor=colors.HexColor('#f0f4f8'), borderPadding=6, leading=16,
    )

    cleaned    = strip_markdown(response or "")
    paragraphs = [p.strip() for p in cleaned.split('\n\n') if p.strip()]

    content = [
        Paragraph("Investigation Report", styles['Title']),
        Paragraph(f"Alert Type: {html.escape(alert_type)}", styles['Heading2']),
        Spacer(1, 0.1 * inch),
        Paragraph(f"Scenario: {html.escape((prompt or '').strip())}", styles['BodyText']),
        Spacer(1, 0.2 * inch),
        Paragraph("Analyst Summary", styles['Heading2']),
        Spacer(1, 0.05 * inch),
        Paragraph("WHAT IS HAPPENING", section_style),
        Paragraph(html.escape(what_happening), box_style),
        Paragraph("WHAT LIKELY ALREADY OCCURRED", section_style),
        Paragraph(html.escape(what_occurred), box_style),
        Paragraph("WHAT TO EXPECT NEXT", section_style),
        Paragraph(html.escape(what_next), box_style),
        Spacer(1, 0.25 * inch),
        Paragraph("Full Investigation Detail", styles['Heading2']),
        Spacer(1, 0.05 * inch),
    ]
    for para in paragraphs:
        content.append(Paragraph(para, styles['BodyText']))
        content.append(Spacer(1, 0.08 * inch))

    doc.build(content)
    return send_file(
        filename,
        as_attachment=True,
        download_name=f"investigation_{conv_id}_{alert_type.lower().replace(' ', '_')}.pdf"
    )


# ---------------------------------------------------------------------------
# Query library (unchanged)
# ---------------------------------------------------------------------------

@app.route("/library", methods=["GET"])
def get_library():
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, query FROM query_library ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/library", methods=["POST"])
def save_library():
    data  = request.get_json()
    name  = data.get("name", "").strip()
    query = data.get("query", "").strip()
    if not name or not query:
        return jsonify({"error": "Name and query are required"}), 400
    conn = _db()
    conn.execute("INSERT INTO query_library (name, query) VALUES (?, ?)", (name, query))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved"})


@app.route("/library/<int:id>", methods=["PATCH"])
def update_library(id):
    data  = request.get_json()
    name  = data.get("name", "").strip()
    query = data.get("query", "").strip()
    conn  = _db()
    if name:
        conn.execute("UPDATE query_library SET name=? WHERE id=?", (name, id))
    if query:
        conn.execute("UPDATE query_library SET query=? WHERE id=?", (query, id))
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})


@app.route("/library/<int:id>", methods=["DELETE"])
def delete_library(id):
    conn = _db()
    conn.execute("DELETE FROM query_library WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# KQL review
# ---------------------------------------------------------------------------

@app.route("/review_query", methods=["POST"])
def review_query():
    data  = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    # Mask the query before sending to Claude
    sm = {}
    masked_query = mask(query, sm)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system="""You are a Microsoft Sentinel KQL expert.
When given a KQL query, you must:
1. State clearly whether the syntax is valid or not.
2. List any errors or inefficiencies found.
3. Suggest a corrected or improved version if needed.
4. Briefly explain what the query does in plain English.
Be concise and direct. Use markdown.""",
            messages=[{"role": "user", "content": f"Review this KQL query:\n\n```kql\n{masked_query}\n```"}]
        )
        result = response.content[0].text
        return jsonify({"response": unmask(result, sm)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@app.route("/reset", methods=["POST"])
def reset():
    _state['conversation_id'] = None
    _state['conversation_history'] = []
    _state['mask_session'] = {}
    return jsonify({"status": "Conversation reset"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
