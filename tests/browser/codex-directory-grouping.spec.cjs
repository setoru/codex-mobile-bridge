'use strict';
const {test, expect} = require('@playwright/test');

const entryId = 'codex:' + 'b'.repeat(32);
const longPath = '/work/' + '很长的项目目录'.repeat(30) + '/long-project';

function sessionRows(host = 'local') {
  return Array.from({length: 151}, (_, index) => {
    const cwd = index === 150 ? null : index === 2 ? longPath : index % 2 ? '/backup/shop' : '/work/shop';
    return {
      id: '12345678-1234-4234-9234-' + String(index).padStart(12, '0'), host,
      title: 'Directory fixture ' + index, cwd, recency: 200000 - index,
      hostLabel: host === 'local' ? '此电脑' : '独立 Codex',
      projectKey: host + '|unassigned', projectName: '未归类', bridgePinned: index === 100,
      directoryGroup: {key: JSON.stringify(['fixture-directory', host, cwd]),
        name: cwd ? cwd.slice(cwd.lastIndexOf('/') + 1) : null, path: cwd, missing: !cwd},
      access: {source: ['cli', 'ide', 'desktop'][index % 3], runtime: 'cli', canSend: true,
        canReconnect: false, canChangeModel: false, canSelectSkills: false, readOnly: false},
    };
  });
}

async function installGateway(page) {
  const model = {identity: 'a'.repeat(64), reads: [], writes: [], rows: sessionRows()};
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    const output = data => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)});
    if (pathname === '/api/activity') return output({sessions: []});
    if (request.method() !== 'GET') {
      model.writes.push(pathname);
      return route.fulfill({status: 405, contentType: 'application/json', body: '{"error":"Synthetic fixture rejects session writes"}'});
    }
    if (pathname === '/api/auth') return output({authenticated: true, csrf: 'synthetic',
      draftIdentity: model.identity, listCacheIdentity: model.identity, uiLanguage: 'zh', computer: {name: '目录测试电脑'}});
    if (pathname === '/api/clients') return output({clients: [
      {id: 'codex', name: 'Codex', enabled: true, installed: true, configured: true, connected: true},
      {id: entryId, name: '独立 Codex', kind: 'codex-local', enabled: true, installed: true, configured: true, connected: true},
    ]});
    if (pathname === '/api/sessions') {
      const host = url.searchParams.get('host') || 'local', source = url.searchParams.get('source') || 'all';
      const query = url.searchParams.get('q') || '', order = url.searchParams.get('order');
      const offset = Number(url.searchParams.get('offset') || 0), archived = url.searchParams.get('archived') === 'true';
      model.reads.push({host, source, query, order, offset, archived});
      if (model.listGate) await model.listGate;
      const rows = (host === 'local' ? model.rows : sessionRows(host))
        .filter(row => !archived && (source === 'all' || row.access.source === source) &&
          (!query || (row.title + ' ' + row.cwd).includes(query)))
        .sort((a, b) => (order === 'recent' ? Number(b.bridgePinned) - Number(a.bridgePinned) : 0) || b.recency - a.recency);
      return output({sessions: rows.slice(0, offset + 101), availableSources: ['cli', 'desktop', 'ide'], remoteHosts: [], complete: true});
    }
    if (pathname.endsWith('/timeline')) {
      const id = pathname.split('/')[3], host = url.searchParams.get('host') || 'local';
      const row = sessionRows(host).find(row => row.id === id);
      return output({sequence: 1, epoch: id, before: 'saved', hasMore: false, files: [],
        meta: {...row, connected: true, loadingHistory: false, historyComplete: true, status: 'idle', requests: [], submissions: []},
        rows: [{key: 'saved', order: 0, role: 'assistant', version: 'v1', text: 'Synthetic saved history'}]});
    }
    if (pathname.endsWith('/changes')) return route.abort();
    if (pathname.endsWith('/notifications')) return output({available: false});
    if (pathname === '/api/accounts' || pathname.endsWith('/accounts')) return output({accounts: [], activeIds: []});
    if (pathname === '/api/account') return output({visible: false});
    return output({});
  });
  return model;
}

async function ready(page) {
  await page.goto('/?case=success');
  await expect(page.locator('#bootstrap-status')).toBeHidden();
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#sessions .session')).toHaveCount(100);
}

function groupFor(page, cwd) {
  return page.locator('#sessions details.project-group').filter({has: page.getByText(cwd, {exact: true})});
}

test.beforeEach(async ({context, request, baseURL}) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === baseURL
    ? route.continue() : route.abort());
  expect(await (await request.get('/__fixture_health')).json()).toEqual({fixture: 'cmb-browser-regression'});
});

test('Codex directory grouping keeps project classification available', async ({page}) => {
  const model = await installGateway(page);
  await ready(page);
  await expect(page.locator('#list-mode option[value="directory"]')).toHaveCount(1);
  await expect(page.locator('#list-mode option[value="project"]')).toHaveCount(1);
  await expect(page.locator('#list-mode option[value="recent"]')).toHaveCount(1);
  await page.selectOption('#list-mode', 'directory');
  await expect(page.locator('#sessions details.project-group')).toHaveCount(3);
  await expect(groupFor(page, '/work/shop')).toHaveCount(1);
  await expect(groupFor(page, '/backup/shop')).toHaveCount(1);
  await expect(groupFor(page, longPath)).toHaveCount(1);
  expect(model.reads.at(-1).order).toBe('project');

  const summary = groupFor(page, '/work/shop').locator('summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  await page.locator('#sessions').evaluate(node => {node.scrollTop = node.scrollHeight;});
  await page.locator('#sessions').dispatchEvent('wheel', {deltaY: 500});
  await expect(page.locator('#sessions .session')).toHaveCount(151);
  await expect(page.locator('#sessions details.project-group')).toHaveCount(4);
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  await expect(groupFor(page, '/work/shop').locator('.session')).toHaveCount(74);
  await expect(groupFor(page, '/backup/shop').locator('.session')).toHaveCount(75);
  await expect(page.locator('#sessions details.project-group').last().locator('summary')).toContainText('无工作目录');
  expect(await page.locator('#sessions .session').evaluateAll(nodes => new Set(nodes.map(node => node.dataset.id)).size)).toBe(151);
  expect(model.reads.some(read => read.offset === 100 && read.order === 'project')).toBe(true);

  await page.selectOption('#list-mode', 'project');
  await expect(page.locator('#sessions details.project-group')).toHaveCount(1);
  await expect(page.locator('#sessions summary')).toContainText('未归类');
  await page.selectOption('#list-mode', 'directory');
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  await page.selectOption('#list-mode', 'recent');
  await expect(page.locator('#sessions details.project-group')).toHaveCount(0);
  await expect(page.locator('#sessions .session').first()).toContainText('Directory fixture 100');
  expect(model.writes).toEqual([]);
});

test('Codex directory mode preserves the selected chat draft and filters', async ({page}) => {
  const model = await installGateway(page);
  await ready(page);
  const id = model.rows[0].id;
  await page.locator(`#sessions .session[data-id="${id}"]`).click();
  await expect(page.locator('#messages')).toContainText('Synthetic saved history');
  await page.locator('#message').fill('Keep this unsent directory-switch draft');
  if (await page.locator('#back').isVisible()) await page.locator('#back').click();

  await page.locator('#search').fill('Directory fixture');
  await page.selectOption('#source-filter', 'cli');
  await expect(page.locator('#sessions .session')).toHaveCount(51);
  await page.locator('#archived').check();
  await expect(page.locator('#sessions .session')).toHaveCount(0);
  await page.selectOption('#list-mode', 'directory');
  await expect(page.locator('#sessions .session')).toHaveCount(0);
  await expect(page.locator('#source-filter')).toHaveValue('cli');
  await expect(page.locator('#search')).toHaveValue('Directory fixture');
  await expect(page.locator('#archived')).toBeChecked();
  await page.locator('#archived').uncheck();
  await expect(page.locator('#sessions .session')).toHaveCount(51);
  await expect(page.locator('#sessions details.project-group')).toHaveCount(3);
  await expect(page.locator(`#sessions .session[data-id="${id}"]`)).toHaveClass(/selected/);
  await page.locator(`#sessions .session[data-id="${id}"]`).click();
  await expect(page.locator('#message')).toHaveValue('Keep this unsent directory-switch draft');
  await expect(page.locator('#chat-title')).toContainText('Directory fixture 0');
  expect(model.reads.at(-1)).toMatchObject({source: 'cli', query: 'Directory fixture', order: 'project', archived: false});
  expect(model.writes).toEqual([]);
});

test('Codex directory preferences and readable paths stay scoped to the gateway and entry', async ({page}, testInfo) => {
  const model = await installGateway(page);
  await ready(page);
  await page.selectOption('#list-mode', 'directory');
  await expect(groupFor(page, '/work/shop')).toBeVisible();
  await groupFor(page, '/work/shop').locator('summary').click();
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  await page.reload();
  await expect(page.locator('#list-mode')).toHaveValue('directory');
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');

  await groupFor(page, '/backup/shop').locator('summary').click();
  const pathLabel = groupFor(page, longPath).locator('.directory-group-path');
  await expect(pathLabel).toHaveText(longPath);
  await pathLabel.scrollIntoViewIfNeeded();
  const bounds = await pathLabel.evaluate(node => ({
    pathFits: node.scrollWidth <= node.clientWidth + 1,
    pageFits: document.documentElement.scrollWidth <= innerWidth + 1,
    wraps: node.getBoundingClientRect().height > parseFloat(getComputedStyle(node).lineHeight),
  }));
  expect(bounds.pathFits).toBe(true);
  expect(bounds.pageFits).toBe(true);
  expect(bounds.wraps).toBe(true);
  await testInfo.attach('directory-groups', {body: await page.screenshot(), contentType: 'image/png'});

  await page.locator(`#client-switch [data-client-id="${entryId}"]`).click();
  await expect(page.locator('#list-mode')).toHaveValue('recent');
  await expect(page.locator('#sessions .session').first()).toHaveAttribute('data-host', entryId);
  await page.selectOption('#list-mode', 'directory');
  await expect(groupFor(page, '/work/shop')).toHaveAttribute('open');
  await page.selectOption('#list-mode', 'project');
  await page.locator('#client-switch [data-client-id="codex"]').click();
  await expect(page.locator('#list-mode')).toHaveValue('directory');
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  await page.locator(`#client-switch [data-client-id="${entryId}"]`).click();
  await expect(page.locator('#list-mode')).toHaveValue('project');
  await page.locator('#client-switch [data-client-id="codex"]').click();

  model.identity = 'c'.repeat(64);
  await page.reload();
  await expect(page.locator('#list-mode')).toHaveValue('recent');
  await page.selectOption('#list-mode', 'directory');
  await expect(groupFor(page, '/work/shop')).toHaveAttribute('open');
  model.identity = 'a'.repeat(64);
  await page.reload();
  await expect(page.locator('#list-mode')).toHaveValue('directory');
  await expect(groupFor(page, '/work/shop')).not.toHaveAttribute('open');
  expect(model.writes).toEqual([]);
});

test('Codex directory mode distinguishes old metadata from a genuinely missing working directory', async ({page}) => {
  const model = await installGateway(page);
  await ready(page);
  await page.selectOption('#list-mode', 'directory');
  await expect(page.locator('#sessions details.project-group')).toHaveCount(3);
  await page.evaluate(() => {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (!key.startsWith('codex-list-v1:')) continue;
      const snapshot = JSON.parse(localStorage.getItem(key));
      snapshot.rows.forEach(row => {delete row.directoryGroup;});
      localStorage.setItem(key, JSON.stringify(snapshot));
    }
  });
  let release;
  model.listGate = new Promise(resolve => {release = resolve;});
  const readCount = model.reads.length;
  try {
    await page.reload();
    await expect.poll(() => model.reads.length).toBeGreaterThan(readCount);
    await expect(page.locator('#sessions .session')).toHaveCount(0);
    await expect(page.locator('#sessions')).not.toContainText('无工作目录');
  } finally {
    model.listGate = null;
    release();
  }
  await expect(page.locator('#sessions details.project-group')).toHaveCount(3);
  const {directoryGroup: _oldMetadata, ...oldRow} = model.rows[0];
  model.rows = [oldRow, model.rows[1], model.rows[150]];
  await page.selectOption('#list-mode', 'recent');
  await expect(page.locator('#sessions .session')).toHaveCount(3);
  await page.selectOption('#list-mode', 'directory');
  await expect(page.locator('#sessions .directory-group-notice')).toContainText('暂不支持按工作目录分类');
  await expect(page.locator('#sessions .session')).toHaveCount(2);
  await expect(page.locator('#sessions details.project-group').last().locator('summary')).toContainText('无工作目录');
  await page.selectOption('#list-mode', 'project');
  await expect(page.locator('#sessions .session')).toHaveCount(3);
  expect(model.writes).toEqual([]);
});

async function toolbarGeometry(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.evaluate(() => Object.fromEntries([
    ['toolbar', '#sidebar .chat-list-toolbar'], ['search', '#search'],
    ['filters', '#sidebar .list-filters'], ['mode', '#list-mode'], ['source', '#source-filter'],
  ].map(([name, selector]) => {
    const rect = document.querySelector(selector).getBoundingClientRect();
    return [name, {x: rect.x, y: rect.y, width: rect.width, height: rect.height}];
  })));
}

function expectSameToolbar(before, after, context) {
  for (const key of Object.keys(before)) {
    for (const dimension of ['x', 'y', 'width', 'height']) {
      expect(Math.abs(before[key][dimension] - after[key][dimension]),
        `${context}: ${key}.${dimension}`).toBeLessThanOrEqual(1);
    }
  }
}

test('Codex toolbar keeps its row when directory mode changes', async ({page}) => {
  const model = await installGateway(page);
  await ready(page);
  for (const language of ['zh', 'en']) {
    await page.evaluate(language => BridgeI18n.setLanguage(language), language);
    for (const width of [1280, 320, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.selectOption('#list-mode', 'recent');
      const before = await toolbarGeometry(page);
      for (const mode of ['project', 'directory', 'recent']) {
        await page.selectOption('#list-mode', mode);
        expectSameToolbar(before, await toolbarGeometry(page), `${language}/${width}/${mode}`);
      }
      for (const source of ['cli', 'ide', 'desktop', 'all']) {
        await page.selectOption('#source-filter', source);
        expectSameToolbar(before, await toolbarGeometry(page), `${language}/${width}/${source}`);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    }
  }
  expect(model.writes).toEqual([]);
});

test('Codex toolbar updates sizing for changed options and larger localized text', async ({page}) => {
  const model = await installGateway(page);
  await ready(page);
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => BridgeI18n.setLanguage('en'));
  await page.selectOption('#list-mode', 'project');
  await expect(page.locator('#source-filter option[value="all"]')).toHaveText('All');
  const initial = await toolbarGeometry(page);
  await page.evaluate(() => {
    // Models a later source-label update after the toolbar has already bound.
    document.querySelector('#source-filter option[value="all"]').textContent = 'All workspace sessions';
  });
  await expect.poll(async () => (await toolbarGeometry(page)).source.width).toBeGreaterThan(initial.source.width);
  const updated = await toolbarGeometry(page);
  await page.selectOption('#source-filter', 'ide');
  expectSameToolbar(updated, await toolbarGeometry(page), 'late option/ide');
  await page.selectOption('#source-filter', 'all');

  await page.evaluate(() => {
    for (const select of document.querySelectorAll('#sidebar .chat-list-toolbar select')) select.style.fontSize = '18px';
  });
  await page.setViewportSize({width: 400, height: 844});
  const enlarged = await toolbarGeometry(page);
  expect(enlarged.mode.width).toBeGreaterThan(updated.mode.width);
  await page.selectOption('#list-mode', 'directory');
  expectSameToolbar(enlarged, await toolbarGeometry(page), 'enlarged/directory');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(model.writes).toEqual([]);
});
