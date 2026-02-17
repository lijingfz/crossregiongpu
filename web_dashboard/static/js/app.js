/**
 * GPU Scheduler Web Dashboard — Chat Frontend
 *
 * Requirements: 1.3, 1.5, 2.2, 2.3, 2.4, 2.5, 3.1, 3.4, 4.2
 */

(function () {
  'use strict';

  // --- State ---
  let sessionId = crypto.randomUUID
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
        var r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
  let pendingApproval = false;

  // --- DOM refs ---
  const messagesEl = document.getElementById('messages');
  const loadingEl = document.getElementById('loading');
  const inputEl = document.getElementById('msg-input');
  const sendBtn = document.getElementById('btn-send');
  const logoutBtn = document.getElementById('btn-logout');
  const displayUser = document.getElementById('display-user');
  const sessionListEl = document.getElementById('session-list');
  const newChatBtn = document.getElementById('btn-new-chat');

  // --- Auth helpers ---
  function getToken() { return localStorage.getItem('token'); }

  function requireAuth() {
    if (!getToken()) { window.location.href = '/login'; }
  }

  function authHeaders() {
    return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() };
  }

  function handleUnauthorized(res) {
    if (res.status === 401 || res.status === 403) {
      localStorage.removeItem('token');
      localStorage.removeItem('username');
      window.location.href = '/login';
      return true;
    }
    return false;
  }

  // --- UI helpers ---
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showLoading(show) {
    loadingEl.classList.toggle('visible', show);
    if (show) scrollToBottom();
  }

  function setInputEnabled(enabled) {
    inputEl.disabled = !enabled;
    sendBtn.disabled = !enabled;
  }

  function appendMessage(role, content, isError) {
    const div = document.createElement('div');
    if (isError) {
      div.className = 'msg msg-error';
    } else {
      div.className = 'msg msg-' + role;
    }
    div.textContent = content;
    messagesEl.appendChild(div);
    scrollToBottom();
  }

  function appendApprovalCard(interrupts) {
    interrupts.forEach(function (intr) {
      var card = document.createElement('div');
      card.className = 'approval-card';

      var reason = document.createElement('div');
      reason.className = 'reason';
      reason.textContent = '审批请求: ' + (intr.reason || '需要您的审批');
      card.appendChild(reason);

      var actions = document.createElement('div');
      actions.className = 'actions';

      var approveBtn = document.createElement('button');
      approveBtn.className = 'btn-approve';
      approveBtn.textContent = '批准';
      approveBtn.addEventListener('click', function () { handleApproval(intr.interrupt_id, 'approved', card); });

      var rejectBtn = document.createElement('button');
      rejectBtn.className = 'btn-reject';
      rejectBtn.textContent = '拒绝';
      rejectBtn.addEventListener('click', function () { handleApproval(intr.interrupt_id, 'rejected', card); });

      actions.appendChild(approveBtn);
      actions.appendChild(rejectBtn);
      card.appendChild(actions);
      messagesEl.appendChild(card);
    });
    scrollToBottom();
  }

  // --- API calls ---
  async function sendMessage() {
    var text = inputEl.value.trim();
    if (!text || pendingApproval) return;

    appendMessage('user', text);
    inputEl.value = '';
    setInputEnabled(false);
    showLoading(true);

    try {
      var res = await fetch('/api/chat/send', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });
      if (handleUnauthorized(res)) return;
      var body = await res.json();
      showLoading(false);

      if (body.status === 'error') {
        appendMessage('assistant', body.message || '请求失败', true);
        setInputEnabled(true);
        return;
      }

      var data = body.data || {};
      if (data.agent_status === 'completed') {
        appendMessage('assistant', data.result || '');
        setInputEnabled(true);
      } else if (data.agent_status === 'approval_required') {
        pendingApproval = true;
        setInputEnabled(false);
        appendApprovalCard(data.interrupts || []);
      } else if (data.agent_status === 'error') {
        appendMessage('assistant', data.error_message || 'Agent 错误', true);
        setInputEnabled(true);
      }
      // Refresh sidebar to show this session
      loadRecentSessions();
    } catch (err) {
      showLoading(false);
      appendMessage('assistant', '网络错误，请稍后重试', true);
      setInputEnabled(true);
    }
  }

  async function handleApproval(interruptId, decision, cardEl) {
    var btns = cardEl.querySelectorAll('button');
    btns.forEach(function (b) { b.disabled = true; });
    showLoading(true);

    try {
      var res = await fetch('/api/chat/approve', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ session_id: sessionId, interrupt_id: interruptId, decision: decision }),
      });
      if (handleUnauthorized(res)) return;
      var body = await res.json();
      showLoading(false);
      pendingApproval = false;

      // Mark card as handled
      cardEl.style.opacity = '0.6';

      if (body.status === 'error') {
        appendMessage('assistant', body.message || '审批失败', true);
      } else {
        var data = body.data || {};
        if (data.agent_status === 'completed') {
          appendMessage('assistant', data.result || '');
        } else if (data.agent_status === 'error') {
          appendMessage('assistant', data.error_message || 'Agent 错误', true);
        }
      }
      setInputEnabled(true);
    } catch (err) {
      showLoading(false);
      appendMessage('assistant', '网络错误，请稍后重试', true);
      pendingApproval = false;
      setInputEnabled(true);
    }
  }

  async function loadHistory() {
    try {
      var res = await fetch('/api/chat/history?session_id=' + encodeURIComponent(sessionId), {
        headers: authHeaders(),
      });
      if (handleUnauthorized(res)) return;
      var body = await res.json();
      if (body.status === 'success' && body.data && body.data.messages) {
        body.data.messages.forEach(function (m) {
          appendMessage(m.role, m.content);
        });
      }
    } catch (err) {
      // Silent — history load failure is non-critical
    }
  }

  // --- Sidebar: recent sessions ---
  function generateUUID() {
    return crypto.randomUUID
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
          var r = Math.random() * 16 | 0;
          return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
  }

  function formatTime(isoStr) {
    if (!isoStr) return '';
    try {
      var d = new Date(isoStr);
      var now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
      }
      return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
    } catch (e) { return ''; }
  }

  async function loadRecentSessions() {
    try {
      var res = await fetch('/api/chat/sessions', { headers: authHeaders() });
      if (handleUnauthorized(res)) return;
      var body = await res.json();
      if (body.status === 'success' && body.data && body.data.sessions) {
        renderSessionList(body.data.sessions);
      }
    } catch (err) {
      // Silent — sidebar load failure is non-critical
    }
  }

  function renderSessionList(sessions) {
    sessionListEl.innerHTML = '';
    sessions.forEach(function (s) {
      var li = document.createElement('li');
      li.dataset.sessionId = s.session_id;
      if (s.session_id === sessionId) li.className = 'active';

      var preview = document.createElement('span');
      preview.textContent = s.preview || '(空对话)';
      li.appendChild(preview);

      var time = document.createElement('span');
      time.className = 'session-time';
      time.textContent = formatTime(s.updated_at);
      li.appendChild(time);

      li.addEventListener('click', function () { switchSession(s.session_id); });
      sessionListEl.appendChild(li);
    });
  }

  function switchSession(newSessionId) {
    if (newSessionId === sessionId) return;
    sessionId = newSessionId;
    messagesEl.innerHTML = '';
    pendingApproval = false;
    setInputEnabled(true);
    loadHistory();
    // Update active state in sidebar
    var items = sessionListEl.querySelectorAll('li');
    items.forEach(function (li) {
      li.className = li.dataset.sessionId === sessionId ? 'active' : '';
    });
  }

  function startNewChat() {
    sessionId = generateUUID();
    messagesEl.innerHTML = '';
    pendingApproval = false;
    setInputEnabled(true);
    // Deselect all sidebar items
    var items = sessionListEl.querySelectorAll('li');
    items.forEach(function (li) { li.className = ''; });
    inputEl.focus();
  }

  // --- Event listeners ---
  sendBtn.addEventListener('click', sendMessage);

  // Track IME composition state to avoid submitting during input method use
  let isComposing = false;
  inputEl.addEventListener('compositionstart', function () { isComposing = true; });
  inputEl.addEventListener('compositionend', function () { isComposing = false; });

  inputEl.addEventListener('keydown', function (e) {
    if (isComposing) return;
    // Ctrl+Enter (Windows/Linux) or Cmd+Enter (macOS) to submit
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
    }
  });

  logoutBtn.addEventListener('click', function () {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    window.location.href = '/login';
  });

  newChatBtn.addEventListener('click', startNewChat);

  // --- Init ---
  requireAuth();
  displayUser.textContent = localStorage.getItem('username') || '';
  loadRecentSessions();
  loadHistory();
})();
