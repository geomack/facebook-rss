'use strict';

const api = 'https://api.github.com/repos/geomack/facebook-rss';
const byId = id => document.getElementById(id);
const progress = byId('progress');
const addForm = byId('add');
const table = byId('sources');
const tokenField = byId('token');
const errorLabels = {
  FACEBOOK_LOGIN_REQUIRED: 'Facebook требует вход',
  FACEBOOK_PAGE_UNAVAILABLE: 'Facebook не открыл страницу',
  TELEGRAM_FETCH_FAILED: 'Telegram не открыл канал',
  SOURCE_FETCH_FAILED: 'Ошибка чтения источника'
};
let busy = false;
let sources = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function sourceStatus(row) {
  if (!row.enabled) return 'Отключён';
  if (row.error) {
    const label = errorLabels[row.error] || 'Ошибка чтения источника';
    return row.access_attempts?.length > 1 ? label + ' (обе попытки)' : label;
  }
  if (!row.checked) return 'Нет результата';
  if (row.found === 0) return 'Записи не найдены';
  if (row.selected === 0) return 'Ни одной подходящей записи';
  if (row.new || row.updated) return 'Есть изменения';
  return 'Без изменений';
}

function render() {
  table.replaceChildren();
  for (const row of sources) {
    const tr = table.insertRow();
    const label = tr.insertCell();
    const title = document.createElement('strong');
    title.textContent = row.name;
    const address = document.createElement('small');
    address.textContent = (row.feed === 'facebook' ? 'Facebook · ' : 'Telegram · @') + row.source;
    label.append(title, document.createElement('br'), address);
    if (row.latest) {
      const url = new URL(row.latest.url);
      if (url.protocol === 'https:' && ['t.me', 'www.facebook.com'].includes(url.hostname)) {
        const latest = document.createElement('a');
        latest.href = url.href;
        latest.textContent = row.latest.title;
        latest.target = '_blank';
        latest.rel = 'noopener noreferrer';
        const date = document.createElement('small');
        date.textContent = new Date(row.latest.date).toLocaleString('ru-RU');
        label.append(document.createElement('br'), latest, document.createElement('br'), date);
      }
    }
    const state = tr.insertCell();
    state.textContent = sourceStatus(row);
    state.className = row.error ? 'status-error' : row.checked ? 'status-ok' : '';
    for (const value of [row.found, row.new, row.updated, row.in_feed]) {
      tr.insertCell().textContent = row.checked || value ? String(value) : '—';
    }
    const button = document.createElement('button');
    button.textContent = row.enabled ? 'Отключить' : 'Включить';
    button.disabled = busy;
    button.addEventListener('click', () => run({
      action: row.enabled ? 'remove' : 'add', feed: row.feed, source: row.source
    }));
    tr.insertCell().append(button);
  }
}

async function report() {
  const response = await fetch('sources.json?check=' + Date.now(), { cache: 'no-store' });
  if (!response.ok) throw new Error('Отчёт ещё не опубликован.');
  const data = await response.json();
  sources = data.sources;
  byId('last').textContent = 'Последняя опубликованная сборка • ' +
    new Date(data.built_at).toLocaleString('ru-RU');
  render();
  return data;
}

async function github(path, token, body) {
  const response = await fetch(api + path, {
    method: body ? 'POST' : 'GET',
    cache: 'no-store',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: 'Bearer ' + token,
      'X-GitHub-Api-Version': '2026-03-10',
      ...(body && { 'Content-Type': 'application/json' })
    },
    ...(body && { body: JSON.stringify(body) })
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error('GitHub ' + response.status + ': ' + (error.message || 'ошибка запроса'));
  }
  return response.status === 204 ? {} : response.json();
}

async function run(inputs) {
  if (busy) return false;
  const token = tokenField.value.trim();
  if (!token) {
    byId('access').open = true;
    tokenField.focus();
    progress.textContent = 'Вставь токен GitHub для этой вкладки.';
    return false;
  }
  busy = true;
  byId('refresh').disabled = addForm.querySelector('button').disabled = true;
  render();
  let runId;
  try {
    progress.textContent = 'Запускаю сборку…';
    const started = await github('/actions/workflows/rss.yml/dispatches', token, {
      ref: 'main', inputs, return_run_details: true
    });
    runId = started.workflow_run_id;
    if (!runId) throw new Error('GitHub принял запуск. Его результат появится в таблице после публикации.');
    const expires = Date.now() + 12 * 60 * 1000;
    while (Date.now() < expires) {
      await delay(6000);
      const data = await github('/actions/runs/' + runId, token);
      if (data.status !== 'completed') {
        progress.textContent = data.status === 'in_progress' ? 'Проверяю источники и собираю RSS…' : 'Сборка в очереди…';
        continue;
      }
      if (data.conclusion !== 'success') throw new Error(
        data.conclusion === 'cancelled' ? 'Запуск отменён.' : 'Сборка завершилась с ошибкой (' + data.conclusion + ').'
      );
      progress.textContent = 'Сборка готова. Жду публикации RSS…';
      const published = await report().catch(() => null);
      if (published && Number(published.run_id) === Number(runId)) {
        progress.textContent = 'RSS опубликован. Новых записей: ' +
          sources.reduce((total, row) => total + row.new, 0) + '.';
        return true;
      }
    }
    throw new Error('Публикация ещё не подтверждена. Таблица продолжит обновляться.');
  } catch (error) {
    progress.textContent = error.message;
    if (runId) {
      const link = document.createElement('a');
      link.href = 'https://github.com/geomack/facebook-rss/actions/runs/' + runId;
      link.textContent = 'Открыть запуск';
      progress.append(' ', link);
    }
    return false;
  } finally {
    busy = false;
    byId('refresh').disabled = addForm.querySelector('button').disabled = false;
    render();
  }
}

byId('feed').addEventListener('change', () => {
  byId('source').placeholder = byId('feed').value === 'facebook'
    ? 'Ссылка на страницу Facebook' : '@username или ссылка t.me';
});
addForm.addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(addForm);
  if (await run({action: 'add', ...Object.fromEntries(form)})) {
    addForm.elements.source.value = '';
    addForm.elements.name.value = '';
  }
});
byId('refresh').addEventListener('click', () => run({action: 'refresh'}));
function refreshReport() {
  if (!busy) report().catch(error => { byId('last').textContent = error.message; });
}
refreshReport();
setInterval(refreshReport, 20000);
