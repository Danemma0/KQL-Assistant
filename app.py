from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from flask import Flask, render_template, request, jsonify, send_file
from anthropic import Anthropic  # type: ignore[import]
from database import init_db
from system_prompt import SYSTEM_PROMPT
from dotenv import load_dotenv
import os
import re
import html
import sqlite3

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

# Store conversation history per session (in-memory for now)
conversation_history = []

# Main route
@app.route("/")
def index():
    return render_template("index.html")

# Route to handle user queries
@app.route("/query", methods=["POST"])
def query():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    alert_type = data.get("alert_type", "General")
    reset = data.get("reset", False)

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Reset conversation if requested
    if reset:
        conversation_history.clear()

    # Build the user message with alert type context
    full_message = f"[Alert Type: {alert_type}]\n\n{user_message}"
    conversation_history.append({
        "role": "user",
        "content": full_message
    })

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=conversation_history
        )

        assistant_message = response.content[0].text

        conn = sqlite3.connect("kql_assistant.db")

        conn.execute("""
        INSERT INTO investigations
        (alert_type,prompt,response)
        VALUES(?,?,?)
        """, (
            alert_type,
            user_message,
            assistant_message
        ))

        conn.commit()
        conn.close()

        conversation_history.append({
            "role": "assistant",
            "content": assistant_message
        })

        return jsonify({
            "response": assistant_message,
            "history_length": len(conversation_history)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()

        return jsonify({
            "error": str(e),
            "type": type(e).__name__
        }), 500

# Route to fetch conversation history
@app.route("/history")
def history():

    conn = sqlite3.connect("kql_assistant.db")

    rows = conn.execute("""
    SELECT id,
           alert_type,
           prompt,
           created_at
    FROM investigations
    ORDER BY created_at DESC
    LIMIT 50
    """).fetchall()

    conn.close()

    return jsonify(rows)

# Route to export a specific investigation to PDF
@app.route("/export/<int:id>")
def export(id):
    import tempfile

    conn = sqlite3.connect("kql_assistant.db")
    row = conn.execute("""
        SELECT alert_type, prompt, response
        FROM investigations
        WHERE id=?
    """, (id,)).fetchone()

    if row is None:
        conn.close()
        return jsonify({"error": "Investigation not found"}), 404
    conn.close()

    # --- Generate 3-part analyst summary via Claude ---
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
                "content": f"Alert Type: {row[0]}\n\nScenario: {row[1]}\n\nInvestigation findings:\n{row[2][:2000]}"
            }]
        )
        analyst_summary = summary_response.content[0].text.strip()
    except Exception:
        analyst_summary = "Summary could not be generated."

    # --- Parse the 3 sections ---
    def extract_section(text, heading):
        pattern = rf'{heading}\s*\n(.*?)(?=\n[A-Z ]+\n|$)'
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    what_happening = extract_section(analyst_summary, "WHAT IS HAPPENING")
    what_occurred  = extract_section(analyst_summary, "WHAT LIKELY ALREADY OCCURRED")
    what_next      = extract_section(analyst_summary, "WHAT TO EXPECT NEXT")

    # --- Build PDF ---
    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    filename = temp_file.name
    temp_file.close()

    doc = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()

    # Custom styles
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    section_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading3'],
        textColor=colors.HexColor('#1a3a5c'),
        spaceAfter=4,
        spaceBefore=10,
    )

    box_style = ParagraphStyle(
        'BoxText',
        parent=styles['BodyText'],
        backColor=colors.HexColor('#f0f4f8'),
        borderPadding=6,
        leading=16,
    )

    cleaned = strip_markdown(row[2])
    paragraphs = [p.strip() for p in cleaned.split('\n\n') if p.strip()]

    content = [
        Paragraph("Investigation Report", styles['Title']),
        Paragraph(f"Alert Type: {html.escape(row[0])}", styles['Heading2']),
        Spacer(1, 0.1 * inch),
        Paragraph(f"Scenario: {html.escape(row[1].strip())}", styles['BodyText']),
        Spacer(1, 0.2 * inch),

        # --- Analyst Summary Block ---
        Paragraph("Analyst Summary", styles['Heading2']),
        Spacer(1, 0.05 * inch),

        Paragraph("WHAT IS HAPPENING", section_style),
        Paragraph(html.escape(what_happening), box_style),

        Paragraph("WHAT LIKELY ALREADY OCCURRED", section_style),
        Paragraph(html.escape(what_occurred), box_style),

        Paragraph("WHAT TO EXPECT NEXT", section_style),
        Paragraph(html.escape(what_next), box_style),

        Spacer(1, 0.25 * inch),

        # --- Full Investigation Detail ---
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
        download_name=f"investigation_{id}_{row[0].lower().replace(' ', '_')}.pdf"
    )

# Get all saved library queries
@app.route("/library", methods=["GET"])
def get_library():
    conn = sqlite3.connect("kql_assistant.db")
    rows = conn.execute("""
        SELECT id, name, query FROM query_library
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([{"id": r[0], "name": r[1], "query": r[2]} for r in rows])

# Save a new library query
@app.route("/library", methods=["POST"])
def save_library():
    data = request.get_json()
    name  = data.get("name", "").strip()
    query = data.get("query", "").strip()
    if not name or not query:
        return jsonify({"error": "Name and query are required"}), 400
    conn = sqlite3.connect("kql_assistant.db")
    conn.execute("INSERT INTO query_library (name, query) VALUES (?, ?)", (name, query))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved"})

# Rename or update a library query
@app.route("/library/<int:id>", methods=["PATCH"])
def update_library(id):
    data  = request.get_json()
    name  = data.get("name", "").strip()
    query = data.get("query", "").strip()
    conn  = sqlite3.connect("kql_assistant.db")
    if name:
        conn.execute("UPDATE query_library SET name=? WHERE id=?", (name, id))
    if query:
        conn.execute("UPDATE query_library SET query=? WHERE id=?", (query, id))
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})

# Delete a library query
@app.route("/library/<int:id>", methods=["DELETE"])
def delete_library(id):
    conn = sqlite3.connect("kql_assistant.db")
    conn.execute("DELETE FROM query_library WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

# Route to review a KQL query for syntax and efficiency
@app.route("/review_query", methods=["POST"])
def review_query():
    data  = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

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
            messages=[{"role": "user", "content": f"Review this KQL query:\n\n```kql\n{query}\n```"}]
        )
        return jsonify({"response": response.content[0].text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Route to reset conversation history
@app.route("/reset", methods=["POST"])
def reset():
    conversation_history.clear()
    return jsonify({"status": "Conversation reset"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)