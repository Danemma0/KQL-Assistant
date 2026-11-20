// Format the response text with HTML
function formatResponse(text) {
  // First extract and protect KQL blocks before marked parses them
  const kqlBlocks = [];
  text = text.replace(/```(?:kql|kusto)?\n?([\s\S]*?)```/g, (match, code) => {
    const placeholder = `%%KQL_BLOCK_${kqlBlocks.length}%%`;
    kqlBlocks.push(code.trim()); // .trim() removes the blank lines
    return placeholder;
  });

  // Parse remaining markdown
  text = marked.parse(text);

  // Restore KQL blocks as styled widgets
  kqlBlocks.forEach((code, i) => {
    const widget = `
      <div class="kql-wrapper">
        <button class="copy-btn" onclick="copyQuery(this)">Copy Query</button>
        <pre><code class="language-sql">${code}</code></pre>
      </div>`;
    text = text.replace(`<p>%%KQL_BLOCK_${i}%%</p>`, widget);
    text = text.replace(`%%KQL_BLOCK_${i}%%`, widget); // fallback
  });

  setTimeout(() => hljs.highlightAll(), 100);
  return text;
}

// Copy query to clipboard
function copyQuery(button){
    const code =
      button.parentElement
      .querySelector('code')
      .innerText;

    navigator.clipboard.writeText(code);

    button.innerText = 'Copied';

    setTimeout(()=>{
        button.innerText='Copy Query';
    },1500);
}

// Toggle between light and dark theme
function toggleTheme(){
  document.body.classList.toggle(
      'light-theme'
  );

  localStorage.setItem(
      'theme',
      document.body.classList.contains(
          'light-theme'
      )
  );
}

// Load theme preference on page load
document.addEventListener('DOMContentLoaded', () => {

    const theme =
        localStorage.getItem('theme');

    if(theme === 'true'){
        document.body.classList.add(
            'light-theme'
        );
    }

});

// Send user message to server and handle response
async function sendMessage() {
  const input = document.getElementById('user-input');
  const alertType = document.getElementById('alert-type').value;
  const chatWindow = document.getElementById('chat-window');
  const sendBtn = document.getElementById('send-btn');
  const historyCount = document.getElementById('history-count');

  const message = input.value.trim();
  if (!message) return;

  // Add user message to chat
  const userDiv = document.createElement('div');
  const alertClass = alertType.toLowerCase().replace(/\s+/g, '-');

  userDiv.className = `message user-message ${alertClass}`; 
  userDiv.innerHTML = `<strong>You (${alertType})</strong>${message}`;
  chatWindow.appendChild(userDiv);

  // Clear input and disable button
  input.value = '';
  sendBtn.disabled = true;

  // Add loading indicator
  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'message assistant-message loading';
  loadingDiv.innerHTML = '<strong>Assistant</strong>Generating query and investigation steps...';
  chatWindow.appendChild(loadingDiv);
  chatWindow.scrollTop = chatWindow.scrollHeight;

  try {
    const response = await fetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, alert_type: alertType })
    });

    const data = await response.json();

    // Remove loading
    chatWindow.removeChild(loadingDiv);

    if (data.error) {
      const errDiv = document.createElement('div');
      errDiv.className = 'message assistant-message';
      errDiv.innerHTML = `<strong>Error</strong>${data.error}`;
      chatWindow.appendChild(errDiv);
    } else {
      const assistantDiv = document.createElement('div');
      assistantDiv.className = 'message assistant-message';
      assistantDiv.innerHTML = `<strong>Assistant</strong>${formatResponse(data.response)}`;
      chatWindow.appendChild(assistantDiv);
      historyCount.textContent = `Messages in session: ${data.history_length}`;
      loadHistory();
    }

  } catch (err) {
    chatWindow.removeChild(loadingDiv);
    const errDiv = document.createElement('div');
    errDiv.className = 'message assistant-message';
    errDiv.innerHTML = `<strong>Error</strong>Could not reach the server. Is Flask running?`;
    chatWindow.appendChild(errDiv);
  }

  sendBtn.disabled = false;
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

// Reset conversation
async function resetConversation() {
  await fetch('/reset', { method: 'POST' });
  const chatWindow = document.getElementById('chat-window');
  chatWindow.innerHTML = `
    <div class="message assistant-message">
      <strong>Assistant</strong>
      <p>New investigation started. Describe your alert or scenario.</p>
    </div>`;
  document.getElementById('history-count').textContent = 'Messages in session: 0';
}

// Allow Enter key to send (Shift+Enter for new line)
document.addEventListener('DOMContentLoaded', () => {
  loadHistory();
  loadLibrary();
  document.getElementById('user-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
});

// Load history from server and display
async function loadHistory() {
  const response = await fetch('/history');
  const data = await response.json();
  const historyDiv =
      document.getElementById('history-list');
  historyDiv.innerHTML = '';
  data.forEach(item => {
    const div =
        document.createElement('div');
    div.className =
        'history-item';
    div.innerHTML = `
      <strong>${item[1]}</strong><br>
      <small>${item[3]}</small><br>
      ${item[2].substring(0, 40)}…
      <br>
      <a href="/export/${item[0]}" target="_blank" style="font-size:0.75rem;color:#58a6ff;">⬇ Export PDF</a>
    `;
    historyDiv.appendChild(div);
  });
}

// Switch between tabs
function switchTab(tab) {
  document.getElementById('tab-investigations').style.display  = tab === 'investigations' ? 'block'  : 'none';
  document.getElementById('tab-library').style.display         = tab === 'library'        ? 'block'  : 'none';

  // Main panel swaps
  document.getElementById('chat-window').style.display         = tab === 'editor'         ? 'none'   : 'flex';
  document.getElementById('editor-panel').style.display        = tab === 'editor'         ? 'flex'   : 'none';
  document.getElementById('input-area-wrapper').style.display  = tab === 'editor'         ? 'none'   : 'flex';

  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    b.classList.toggle('active',
      (i === 0 && tab === 'investigations') ||
      (i === 1 && tab === 'library')        ||
      (i === 2 && tab === 'editor')
    );
  });

  if (tab === 'library') loadLibrary();
}

// Live validate KQL syntax as user types
function liveValidate() {
  const query = document.getElementById('kql-editor').value.trim();
  const icon  = document.getElementById('validation-icon');
  const msg   = document.getElementById('validation-msg');
  const bar   = document.getElementById('validation-bar');

  if (!query) {
    icon.textContent  = '⬜';
    msg.textContent   = 'Start typing to validate syntax...';
    msg.style.color   = '#8b949e';
    bar.style.borderColor = '#30363d';
    return;
  }

  const errors = validateKQL(query);

  if (errors.length === 0) {
    icon.textContent      = '✅';
    msg.textContent       = 'Structure looks valid (No syntax errors detected).';
    bar.style.borderColor = '#3fb950';
  } else {
    icon.textContent      = '❌';
    msg.innerHTML         = errors.map(e => `• ${e}`).join('<br>');
    msg.style.color       = '#f85149';
    bar.style.borderColor = '#f85149';
  }
}

// Basic KQL validation rules
function validateKQL(query) {
  const errors = [];
  const lines  = query.split('\n').map(l => l.trim()).filter(Boolean);

  // Must start with a table name (no leading pipe)
  if (lines[0].startsWith('|')) {
    errors.push('Query must start with a table name, not a pipe operator.');
  }

  // Unclosed parentheses
  const opens  = (query.match(/\(/g) || []).length;
  const closes = (query.match(/\)/g) || []).length;
  if (opens !== closes) {
    errors.push(`Mismatched parentheses: ${opens} opening vs ${closes} closing.`);
  }

  // Unclosed brackets
  const sqOpen  = (query.match(/\[/g) || []).length;
  const sqClose = (query.match(/\]/g) || []).length;
  if (sqOpen !== sqClose) {
    errors.push(`Mismatched square brackets: ${sqOpen} opening vs ${sqClose} closing.`);
  }

  // Unclosed string literals
  const singleQuotes = (query.match(/'/g) || []).length;
  const doubleQuotes = (query.match(/"/g) || []).length;
  if (singleQuotes % 2 !== 0) errors.push('Odd number of single quotes — possible unclosed string.');
  if (doubleQuotes % 2 !== 0) errors.push('Odd number of double quotes — possible unclosed string.');

  // Pipe lines must have an operator after them
  lines.forEach((line, i) => {
    if (line === '|') {
      errors.push(`Line ${i + 1}: Pipe operator with no following command.`);
    }
  });

  // where clause must have a condition
  lines.forEach((line, i) => {
    if (/^\|\s*where\s*$/.test(line)) {
      errors.push(`Line ${i + 1}: 'where' operator has no condition.`);
    }
  });

  // summarize without by is a warning, not an error — just flag it
  const hasSummarize = lines.some(l => /^\|\s*summarize/.test(l));
  const hasBy        = lines.some(l => /\bby\b/.test(l));
  if (hasSummarize && !hasBy) {
    errors.push("'summarize' detected without 'by' — is this intentional?");
  }

  // Common typos
  const typos = {
    'whare': 'where', 'wehre': 'where', 'porject': 'project',
    'proejct': 'project', 'sumarize': 'summarize', 'distint': 'distinct'
  };
  Object.entries(typos).forEach(([typo, correct]) => {
    if (query.toLowerCase().includes(typo)) {
      errors.push(`Possible typo: '${typo}' — did you mean '${correct}'?`);
    }
  });

  return errors;
}

// Send query to Claude for review
async function claudeReview() {
  const query  = document.getElementById('kql-editor').value.trim();
  const output = document.getElementById('claude-review-output');

  if (!query) {
    alert('Nothing to review (Write or paste a KQL query first.)');
    return;
  }

  output.style.display  = 'block';
  output.innerHTML      = '<span style="color:#8b949e;font-style:italic;">Reviewing with Claude...</span>';

  try {
    const res  = await fetch('/review_query', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query })
    });
    const data = await res.json();
    output.innerHTML = formatResponse(data.response);
  } catch (e) {
    output.innerHTML = '<span style="color:#f85149;">Could not reach server.</span>';
  }
}

// Send editor query to chat for further improvement
function sendEditorToChat() {
  const query = document.getElementById('kql-editor').value.trim();
  if (!query) return;
  switchTab('investigations');
  document.getElementById('user-input').value = `Please review and improve this KQL query:\n\n\`\`\`kql\n${query}\n\`\`\``;
  document.getElementById('user-input').focus();
}

// Save editor query to library
async function saveEditorToLibrary() {
  const name  = document.getElementById('editor-query-name').value.trim();
  const query = document.getElementById('kql-editor').value.trim();
  if (!query) { alert('Nothing to save.'); return; }
  if (!name)  { alert('Please enter a name for this query.'); return; }

  await fetch('/library', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, query })
  });

  document.getElementById('editor-query-name').value = '';
  alert(`"${name}" saved to your Query Library.`);
}

// Clear the editor
function clearEditor() {
  if (!confirm('Clear the editor?')) return;
  document.getElementById('kql-editor').value = '';
  document.getElementById('claude-review-output').style.display = 'none';
  liveValidate();
}

// Load saved custom queries from DB into sidebar
async function loadLibrary() {
  const res  = await fetch('/library');
  const data = await res.json();
  const list = document.getElementById('custom-query-list');
  list.innerHTML = '';

  if (data.length === 0) {
    list.innerHTML = '<p style="font-size:0.75rem;color:#484f58;">No saved queries yet.</p>';
    return;
  }

  data.forEach(item => {
    const div = document.createElement('div');
    div.className = 'library-item custom-query-item';
    div.dataset.query = item.query;
    div.dataset.id    = item.id;
    div.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">
        <span class="query-label" onclick="pasteQuery(this.parentElement.parentElement)"
              style="flex:1;cursor:pointer;">
          📄 ${item.name}
        </span>
        <button onclick="renameQuery(${item.id}, this)" title="Rename"
          style="background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.8rem;padding:2px 4px;">
          ✏️
        </button>
        <button onclick="deleteQuery(${item.id}, this)" title="Delete"
          style="background:none;border:none;color:#8b949e;cursor:pointer;font-size:0.8rem;padding:2px 4px;">
          🗑️
        </button>
      </div>
    `;
    list.appendChild(div);
  });
}

// Save a new custom query
async function saveCustomQuery() {
  const name  = document.getElementById('new-query-name').value.trim();
  const query = document.getElementById('new-query-body').value.trim();
  if (!name || !query) {
    alert('Please enter both a name and a query.');
    return;
  }
  await fetch('/library', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, query })
  });
  document.getElementById('new-query-name').value = '';
  document.getElementById('new-query-body').value = '';
  loadLibrary();
}

// Rename an existing query inline
async function renameQuery(id, btn) {
  const item    = btn.closest('.custom-query-item');
  const label   = item.querySelector('.query-label');
  const current = label.textContent.replace('📄 ', '').trim();
  const newName = prompt('Rename query:', current);
  if (!newName || newName.trim() === current) return;
  await fetch(`/library/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName.trim() })
  });
  loadLibrary();
}

// Delete a query
async function deleteQuery(id, btn) {
  if (!confirm('Delete this query from your library?')) return;
  await fetch(`/library/${id}`, { method: 'DELETE' });
  loadLibrary();
}

// Paste query from library into input
function pasteQuery(el) {
  document.getElementById('user-input').value = el.dataset.query;
  document.getElementById('user-input').focus();
}