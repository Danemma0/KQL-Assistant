// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------
let currentConversationId = null;
let streamAbortController = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safeHTML(text) {
  return DOMPurify.sanitize(text);
}

function escapeText(text) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(String(text ?? '')));
  return d.innerHTML;
}

function getExtraTerms() {
  return document.getElementById('always-mask-terms').value
    .split('\n').map(t => t.trim()).filter(Boolean);
}

function isMaskingOn() {
  return document.getElementById('masking-toggle').checked;
}

// ---------------------------------------------------------------------------
// formatResponse — DOMPurify-safe, copy handled via event delegation
// ---------------------------------------------------------------------------
function formatResponse(text) {
  const kqlBlocks = [];
  text = text.replace(/```(?:kql|kusto)?\n?([\s\S]*?)```/g, (match, code) => {
    const ph = `%%KQL_BLOCK_${kqlBlocks.length}%%`;
    kqlBlocks.push(code.trim());
    return ph;
  });

  text = marked.parse(text);

  kqlBlocks.forEach((code, i) => {
    // No onclick — copy is handled by event delegation on the chat window
    const widget = `
      <div class="kql-wrapper">
        <button class="copy-btn" data-code="${DOMPurify.sanitize(code).replace(/"/g, '&quot;')}">Copy Query</button>
        <pre><code class="language-sql">${escapeText(code)}</code></pre>
      </div>`;
    text = text.replace(`<p>%%KQL_BLOCK_${i}%%</p>`, widget);
    text = text.replace(`%%KQL_BLOCK_${i}%%`, widget);
  });

  setTimeout(() => hljs.highlightAll(), 100);
  // Allow data-code on button — DOMPurify keeps data attributes
  return DOMPurify.sanitize(text, { ADD_ATTR: ['data-code'] });
}

// Event delegation for copy buttons — catches all past and future buttons
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('chat-window').addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    // Try data-code first, fall back to sibling <code> text
    const code = btn.dataset.code || btn.parentElement.querySelector('code')?.innerText || '';
    navigator.clipboard.writeText(code);
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = 'Copy Query'; }, 1500);
  });

  // Close preview modal on overlay click
  document.getElementById('mask-preview-overlay')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeMaskPreview();
  });

  loadHistory();
  loadLibrary();

  document.getElementById('user-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
});

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
function toggleTheme() {
  document.body.classList.toggle('light-theme');
  localStorage.setItem('theme', document.body.classList.contains('light-theme'));
}

document.addEventListener('DOMContentLoaded', () => {
  if (localStorage.getItem('theme') === 'true') document.body.classList.add('light-theme');
});

// ---------------------------------------------------------------------------
// Send message — streams the response
// ---------------------------------------------------------------------------
async function sendMessage() {
  const input      = document.getElementById('user-input');
  const alertType  = document.getElementById('alert-type').value;
  const chatWindow = document.getElementById('chat-window');
  const sendBtn    = document.getElementById('send-btn');
  const stopBtn    = document.getElementById('stop-btn');
  const message    = input.value.trim();
  if (!message) return;

  // User bubble — built with createElement, no innerHTML injection
  const alertClass = alertType.toLowerCase().replace(/\s+/g, '-');
  const userDiv    = document.createElement('div');
  userDiv.className = `message user-message ${alertClass}`;
  const uLabel = document.createElement('strong');
  uLabel.textContent = `You (${alertType})`;
  const uText  = document.createElement('span');
  uText.textContent = message;
  userDiv.appendChild(uLabel);
  userDiv.appendChild(uText);
  chatWindow.appendChild(userDiv);

  input.value      = '';
  sendBtn.disabled = true;
  stopBtn.style.display = 'inline-block';

  // Streaming assistant bubble
  const assistantDiv = document.createElement('div');
  assistantDiv.className = 'message assistant-message loading';
  const aLabel = document.createElement('strong');
  aLabel.textContent = 'Assistant';
  const streamSpan = document.createElement('span');
  streamSpan.id = 'stream-output';
  assistantDiv.appendChild(aLabel);
  assistantDiv.appendChild(streamSpan);
  chatWindow.appendChild(assistantDiv);
  chatWindow.scrollTop = chatWindow.scrollHeight;

  let accumulatedText = '';
  streamAbortController = new AbortController();

  try {
    const response = await fetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        alert_type: alertType,
        conversation_id: currentConversationId,
        masking: isMaskingOn(),
        extra_terms: getExtraTerms(),
      }),
      signal: streamAbortController.signal,
    });

    if (!response.ok) {
      const err = await response.json();
      assistantDiv.className = 'message assistant-message';
      assistantDiv.innerHTML = safeHTML(`<strong>Error</strong>${escapeText(err.error || 'Unknown error')}`);
      return;
    }

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch { continue; }

        if (data.error) {
          assistantDiv.className = 'message assistant-message';
          const eLabel = document.createElement('strong');
          eLabel.textContent = 'Error';
          assistantDiv.innerHTML = '';
          assistantDiv.appendChild(eLabel);
          assistantDiv.appendChild(document.createTextNode(data.error));
          return;
        }

        if (data.token) {
          accumulatedText += data.token;
          streamSpan.textContent = accumulatedText;
          chatWindow.scrollTop   = chatWindow.scrollHeight;
        }

        if (data.done) {
          currentConversationId = data.conversation_id;

          // Final formatted render
          assistantDiv.className = 'message assistant-message';
          const fLabel = document.createElement('strong');
          fLabel.textContent = 'Assistant';
          assistantDiv.innerHTML = '';
          assistantDiv.appendChild(fLabel);
          const contentDiv = document.createElement('div');
          contentDiv.innerHTML = formatResponse(accumulatedText);
          assistantDiv.appendChild(contentDiv);

          // Message count
          const msgCount  = document.getElementById('history-count');
          const prevCount = parseInt(msgCount.textContent.replace(/\D/g, '')) || 0;
          msgCount.textContent = `Messages in session: ${prevCount + 2}`;

          // Masking badge
          if (data.masked_count > 0) {
            const badge = document.getElementById('masking-badge');
            badge.textContent = `🛡 ${data.masked_count} item${data.masked_count === 1 ? '' : 's'} masked`;
            badge.style.display = 'inline-block';
          }

          // Token usage — show input, output, plus cache creation AND cache hits
          if (data.usage) {
            const u       = data.usage;
            const created = u.cache_creation_input_tokens || 0;
            const cached  = u.cache_read_input_tokens     || 0;
            const parts   = [
              `↑ ${u.input_tokens} in`,
              `↓ ${u.output_tokens} out`,
              created ? `⚡ ${created} cached (new)` : '',
              cached  ? `⚡ ${cached} from cache`    : '',
            ].filter(Boolean);
            document.getElementById('token-usage').textContent = parts.join('  ·  ');
          }

          loadHistory();
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      assistantDiv.className = 'message assistant-message';
      assistantDiv.innerHTML = '';
      const abl = document.createElement('strong'); abl.textContent = 'Assistant';
      assistantDiv.appendChild(abl);
      if (accumulatedText) {
        const c = document.createElement('div');
        c.innerHTML = formatResponse(accumulatedText);
        const note = document.createElement('em');
        note.style.color = '#8b949e';
        note.textContent = ' [stopped]';
        c.appendChild(note);
        assistantDiv.appendChild(c);
      } else {
        const note = document.createElement('em');
        note.style.color = '#8b949e';
        note.textContent = 'Response stopped.';
        assistantDiv.appendChild(note);
      }
    } else {
      assistantDiv.className = 'message assistant-message';
      assistantDiv.innerHTML = safeHTML('<strong>Error</strong>Could not reach the server. Is Flask running?');
    }
  } finally {
    sendBtn.disabled      = false;
    stopBtn.style.display = 'none';
    streamAbortController = null;
    chatWindow.scrollTop  = chatWindow.scrollHeight;
  }
}

function stopStream() {
  if (streamAbortController) streamAbortController.abort();
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------
async function resetConversation() {
  await fetch('/reset', { method: 'POST' });
  currentConversationId = null;

  const chatWindow = document.getElementById('chat-window');
  chatWindow.innerHTML = '';
  const welcomeDiv = document.createElement('div');
  welcomeDiv.className = 'message assistant-message';
  welcomeDiv.innerHTML = safeHTML('<strong>Assistant</strong><p>New investigation started. Describe your alert or scenario.</p>');
  chatWindow.appendChild(welcomeDiv);

  document.getElementById('history-count').textContent = 'Messages in session: 0';
  document.getElementById('token-usage').textContent   = '';
  document.getElementById('masking-badge').style.display = 'none';
}

// ---------------------------------------------------------------------------
// History / saved investigations — built with createElement (no inline onclick)
// ---------------------------------------------------------------------------
async function loadHistory(query = '') {
  const url     = '/history' + (query ? `?q=${encodeURIComponent(query)}` : '');
  const res     = await fetch(url);
  const data    = await res.json();
  const listDiv = document.getElementById('history-list');
  listDiv.innerHTML = '';

  // Filter out legacy rows that have no conversation_id (can't be restored)
  const valid = data.filter(item => item.conversation_id);

  if (!valid.length) {
    const p = document.createElement('p');
    p.style.cssText = 'font-size:0.75rem;color:#484f58;';
    p.textContent = 'No investigations yet.';
    listDiv.appendChild(p);
    return;
  }

  valid.forEach(item => {
    const convId     = item.conversation_id;
    const title      = item.title || item.first_prompt || '(untitled)';
    const alertBadge = item.alert_type || '';
    const ts         = item.last_activity ? item.last_activity.substring(0, 16) : '';
    const count      = item.message_count || 1;

    // Outer card
    const card = document.createElement('div');
    card.className = 'history-item';

    // Row
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:space-between;align-items:flex-start;gap:4px;';

    // Info column — click restores
    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0;cursor:pointer;';
    info.addEventListener('click', () => restoreConversation(convId, alertBadge));

    const titleEl = document.createElement('strong');
    titleEl.style.cssText = 'display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    titleEl.title       = title;
    titleEl.textContent = title.substring(0, 45) + (title.length > 45 ? '…' : '');

    const metaEl = document.createElement('small');
    metaEl.style.color   = '#8b949e';
    metaEl.textContent   = `${ts} · ${count} msg${count !== 1 ? 's' : ''}`;

    const alertEl = document.createElement('small');
    alertEl.style.color  = '#58a6ff';
    alertEl.textContent  = alertBadge;

    info.appendChild(titleEl);
    info.appendChild(document.createElement('br'));
    info.appendChild(metaEl);
    info.appendChild(document.createElement('br'));
    info.appendChild(alertEl);

    // Action buttons
    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:2px;flex-shrink:0;align-items:center;';

    const renameBtn = document.createElement('button');
    renameBtn.textContent = '✏️';
    renameBtn.title       = 'Rename';
    renameBtn.style.cssText = 'background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.85rem;padding:2px 4px;';
    renameBtn.addEventListener('click', (e) => { e.stopPropagation(); renameConversation(convId); });

    const deleteBtn = document.createElement('button');
    deleteBtn.textContent = '🗑️';
    deleteBtn.title       = 'Delete';
    deleteBtn.style.cssText = 'background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.85rem;padding:2px 4px;';
    deleteBtn.addEventListener('click', (e) => { e.stopPropagation(); deleteConversation(convId); });

    const exportLink = document.createElement('a');
    exportLink.href        = `/export/${convId}`;
    exportLink.target      = '_blank';
    exportLink.title       = 'Export as PDF';
    exportLink.style.cssText = [
      'display:inline-flex;align-items:center;gap:3px;',
      'padding:3px 7px;border-radius:4px;text-decoration:none;',
      'font-size:0.78rem;font-weight:600;',
      'background:#21262d;border:1px solid #30363d;color:#58a6ff;',
      'white-space:nowrap;cursor:pointer;',
    ].join('');
    exportLink.innerHTML = '&#11015;&#xFE0E; PDF';   // ⬇ PDF, text-only variant
    exportLink.addEventListener('click', (e) => e.stopPropagation());
    exportLink.addEventListener('mouseenter', () => { exportLink.style.borderColor = '#58a6ff'; });
    exportLink.addEventListener('mouseleave', () => { exportLink.style.borderColor = '#30363d'; });

    btns.appendChild(renameBtn);
    btns.appendChild(deleteBtn);
    btns.appendChild(exportLink);

    row.appendChild(info);
    row.appendChild(btns);
    card.appendChild(row);
    listDiv.appendChild(card);
  });
}

async function restoreConversation(convId, alertType) {
  const res  = await fetch(`/conversation/${convId}`);
  const data = await res.json();
  if (!data.messages?.length) return;

  currentConversationId = convId;
  const chatWindow = document.getElementById('chat-window');
  chatWindow.innerHTML = '';

  data.messages.forEach(msg => {
    const div = document.createElement('div');
    if (msg.role === 'user') {
      div.className = 'message user-message';
      const lbl = document.createElement('strong');
      lbl.textContent = `You (${data.alert_type || 'General'})`;
      const txt = document.createElement('span');
      txt.textContent = msg.content;
      div.appendChild(lbl);
      div.appendChild(txt);
    } else {
      div.className = 'message assistant-message';
      const lbl = document.createElement('strong');
      lbl.textContent = 'Assistant';
      const c = document.createElement('div');
      c.innerHTML = formatResponse(msg.content);
      div.appendChild(lbl);
      div.appendChild(c);
    }
    chatWindow.appendChild(div);
  });

  chatWindow.scrollTop = chatWindow.scrollHeight;
  document.getElementById('history-count').textContent =
    `Messages in session: ${data.messages.length}`;

  const sel = document.getElementById('alert-type');
  for (const opt of sel.options) {
    if (opt.value === data.alert_type) { sel.value = data.alert_type; break; }
  }
}

async function renameConversation(convId) {
  const newTitle = prompt('Rename investigation:');
  if (!newTitle?.trim()) return;
  await fetch(`/conversation/${convId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle.trim() }),
  });
  loadHistory(document.getElementById('search-input').value);
}

async function deleteConversation(convId) {
  if (!confirm('Delete this investigation and all its messages?')) return;
  await fetch(`/conversation/${convId}`, { method: 'DELETE' });
  if (currentConversationId === convId) {
    currentConversationId = null;
    const chatWindow = document.getElementById('chat-window');
    chatWindow.innerHTML = '';
    const d = document.createElement('div');
    d.className = 'message assistant-message';
    d.innerHTML = safeHTML('<strong>Assistant</strong><p>New investigation started. Describe your alert or scenario.</p>');
    chatWindow.appendChild(d);
    document.getElementById('history-count').textContent = 'Messages in session: 0';
  }
  loadHistory(document.getElementById('search-input').value);
}

// ---------------------------------------------------------------------------
// Masking preview modal
// ---------------------------------------------------------------------------
async function showMaskPreview() {
  const msg   = document.getElementById('user-input').value.trim();
  const atype = document.getElementById('alert-type').value;
  if (!msg) { alert('Type a message first to preview what will be sent.'); return; }

  const overlay = document.getElementById('mask-preview-overlay');
  const content = document.getElementById('mask-preview-content');
  const countEl = document.getElementById('mask-preview-count');
  content.textContent = 'Loading…';
  countEl.textContent = '';
  overlay.style.display = 'flex';

  if (!isMaskingOn()) {
    content.textContent = `[Alert Type: ${atype}]\n\n${msg}`;
    countEl.textContent = 'Masking is off — no values will be masked.';
    return;
  }

  try {
    const res  = await fetch('/preview_mask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, alert_type: atype, extra_terms: getExtraTerms() }),
    });
    const data = await res.json();
    content.textContent = data.masked_text || msg;
    countEl.textContent = data.new_count
      ? `${data.new_count} new value${data.new_count !== 1 ? 's' : ''} will be masked (${data.count} total in this session).`
      : 'No new sensitive values detected in this message.';
  } catch {
    content.textContent = 'Could not reach server.';
  }
}

function closeMaskPreview() {
  document.getElementById('mask-preview-overlay').style.display = 'none';
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
function switchTab(tab) {
  document.getElementById('tab-investigations').style.display = tab === 'investigations' ? 'block' : 'none';
  document.getElementById('tab-library').style.display        = tab === 'library'        ? 'block' : 'none';
  document.getElementById('chat-window').style.display        = tab === 'editor'         ? 'none'  : 'flex';
  document.getElementById('editor-panel').style.display       = tab === 'editor'         ? 'flex'  : 'none';
  document.getElementById('input-area-wrapper').style.display = tab === 'editor'         ? 'none'  : 'flex';
  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    b.classList.toggle('active',
      (i === 0 && tab === 'investigations') ||
      (i === 1 && tab === 'library')        ||
      (i === 2 && tab === 'editor')
    );
  });
  if (tab === 'library') loadLibrary();
}

// ---------------------------------------------------------------------------
// KQL editor
// ---------------------------------------------------------------------------
function liveValidate() {
  const query = document.getElementById('kql-editor').value.trim();
  const icon  = document.getElementById('validation-icon');
  const msg   = document.getElementById('validation-msg');
  const bar   = document.getElementById('validation-bar');
  if (!query) {
    icon.textContent = '⬜'; msg.textContent = 'Start typing to validate syntax…';
    msg.style.color = '#8b949e'; bar.style.borderColor = '#30363d'; return;
  }
  const errors = validateKQL(query);
  if (errors.length === 0) {
    icon.textContent = '✅'; msg.textContent = 'Structure looks valid (No syntax errors detected).';
    bar.style.borderColor = '#3fb950';
  } else {
    icon.textContent = '❌';
    msg.innerHTML = '';
    errors.forEach(e => {
      const line = document.createElement('span');
      line.textContent = `• ${e}`;
      msg.appendChild(line);
      msg.appendChild(document.createElement('br'));
    });
    msg.style.color = '#f85149'; bar.style.borderColor = '#f85149';
  }
}

function validateKQL(query) {
  const errors = [];
  const lines  = query.split('\n').map(l => l.trim()).filter(Boolean);
  if (lines[0]?.startsWith('|')) errors.push('Query must start with a table name, not a pipe operator.');
  const opens = (query.match(/\(/g)||[]).length, closes = (query.match(/\)/g)||[]).length;
  if (opens !== closes) errors.push(`Mismatched parentheses: ${opens} opening vs ${closes} closing.`);
  const sqO = (query.match(/\[/g)||[]).length, sqC = (query.match(/\]/g)||[]).length;
  if (sqO !== sqC) errors.push(`Mismatched square brackets: ${sqO} opening vs ${sqC} closing.`);
  if ((query.match(/'/g)||[]).length % 2 !== 0) errors.push('Odd number of single quotes — possible unclosed string.');
  if ((query.match(/"/g)||[]).length % 2 !== 0) errors.push('Odd number of double quotes — possible unclosed string.');
  lines.forEach((line, i) => {
    if (line === '|') errors.push(`Line ${i+1}: Pipe operator with no following command.`);
    if (/^\|\s*where\s*$/.test(line)) errors.push(`Line ${i+1}: 'where' operator has no condition.`);
  });
  const typos = { 'whare':'where','wehre':'where','porject':'project','proejct':'project','sumarize':'summarize','distint':'distinct' };
  Object.entries(typos).forEach(([typo, correct]) => {
    if (query.toLowerCase().includes(typo))
      errors.push(`Possible typo: '${typo}' — did you mean '${correct}'?`);
  });
  if (lines.some(l => /^\|\s*summarize/.test(l)) && !lines.some(l => /\bby\b/.test(l)))
    errors.push("'summarize' detected without 'by' — is this intentional?");
  return errors;
}

async function claudeReview() {
  const query  = document.getElementById('kql-editor').value.trim();
  const output = document.getElementById('claude-review-output');
  if (!query) { alert('Nothing to review (Write or paste a KQL query first.)'); return; }
  output.style.display = 'block';
  output.innerHTML     = safeHTML('<span style="color:#8b949e;font-style:italic;">Reviewing with Claude…</span>');
  try {
    const res  = await fetch('/review_query', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    output.innerHTML = formatResponse(data.response || data.error || 'No response');
  } catch {
    output.innerHTML = safeHTML('<span style="color:#f85149;">Could not reach server.</span>');
  }
}

function sendEditorToChat() {
  const query = document.getElementById('kql-editor').value.trim();
  if (!query) return;
  switchTab('investigations');
  document.getElementById('user-input').value =
    `Please review and improve this KQL query:\n\n\`\`\`kql\n${query}\n\`\`\``;
  document.getElementById('user-input').focus();
}

async function saveEditorToLibrary() {
  const name  = document.getElementById('editor-query-name').value.trim();
  const query = document.getElementById('kql-editor').value.trim();
  if (!query) { alert('Nothing to save.'); return; }
  if (!name)  { alert('Please enter a name for this query.'); return; }
  await fetch('/library', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, query }),
  });
  document.getElementById('editor-query-name').value = '';
  alert(`"${name}" saved to your Query Library.`);
}

function clearEditor() {
  if (!confirm('Clear the editor?')) return;
  document.getElementById('kql-editor').value = '';
  document.getElementById('claude-review-output').style.display = 'none';
  liveValidate();
}

// ---------------------------------------------------------------------------
// Query library — built with createElement (no inline onclick)
// ---------------------------------------------------------------------------
async function loadLibrary() {
  const res  = await fetch('/library');
  const data = await res.json();
  const list = document.getElementById('custom-query-list');
  list.innerHTML = '';

  if (!data.length) {
    const p = document.createElement('p');
    p.style.cssText  = 'font-size:0.75rem;color:#484f58;';
    p.textContent    = 'No saved queries yet.';
    list.appendChild(p);
    return;
  }

  data.forEach(item => {
    const div       = document.createElement('div');
    div.className   = 'library-item custom-query-item';

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;gap:6px;';

    const label = document.createElement('span');
    label.className   = 'query-label';
    label.style.cssText = 'flex:1;cursor:pointer;';
    label.textContent = `📄 ${item.name}`;
    label.addEventListener('click', () => pasteQuery(item.query));

    const renameBtn = document.createElement('button');
    renameBtn.textContent   = '✏️';
    renameBtn.title         = 'Rename';
    renameBtn.style.cssText = 'background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.8rem;padding:2px 4px;';
    renameBtn.addEventListener('click', () => renameQuery(item.id));

    const deleteBtn = document.createElement('button');
    deleteBtn.textContent   = '🗑️';
    deleteBtn.title         = 'Delete';
    deleteBtn.style.cssText = 'background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.8rem;padding:2px 4px;';
    deleteBtn.addEventListener('click', () => deleteQuery(item.id));

    row.appendChild(label);
    row.appendChild(renameBtn);
    row.appendChild(deleteBtn);
    div.appendChild(row);
    list.appendChild(div);
  });
}

async function saveCustomQuery() {
  const name  = document.getElementById('new-query-name').value.trim();
  const query = document.getElementById('new-query-body').value.trim();
  if (!name || !query) { alert('Please enter both a name and a query.'); return; }
  await fetch('/library', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, query }),
  });
  document.getElementById('new-query-name').value = '';
  document.getElementById('new-query-body').value = '';
  loadLibrary();
}

async function renameQuery(id) {
  const newName = prompt('Rename query:');
  if (!newName?.trim()) return;
  await fetch(`/library/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName.trim() }),
  });
  loadLibrary();
}

async function deleteQuery(id) {
  if (!confirm('Delete this query from your library?')) return;
  await fetch(`/library/${id}`, { method: 'DELETE' });
  loadLibrary();
}

function pasteQuery(queryText) {
  document.getElementById('user-input').value = queryText;
  document.getElementById('user-input').focus();
}
