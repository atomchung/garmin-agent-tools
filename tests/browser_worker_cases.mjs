// Anonymous fake tabs only. No real CUA, browser, Garmin, login, or provider call.
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';

const root = await fs.mkdtemp(path.join(tmpdir(), 'anonymous-cua-worker-test-'));
try {
  const source = await fs.readFile(process.argv[2], 'utf8');
  const modulePath = path.join(root, 'worker-fixture.mjs');
  await fs.writeFile(modulePath, source + '\nexport {observeQueuedWorkout, accountKeyFromProfileUrl};\n');
  const {observeQueuedWorkout, accountKeyFromProfileUrl} = await import(pathToFileURL(modulePath).href);
  const fakeUrl = 'https://connect.garmin.com/app/profile/ANONYMOUS';
  assert.equal(await accountKeyFromProfileUrl(fakeUrl), process.argv[3]);
  assert.equal(await accountKeyFromProfileUrl(fakeUrl + '?token=FAKE'), null);
  assert.equal(await accountKeyFromProfileUrl('https://example.invalid/app/profile/ANONYMOUS'), null);

  for (const scenario of ['same', 'different', 'loading', 'redirect']) {
    const privateRoot = path.join(root, scenario);
    const runId = randomUUID().replaceAll('-', '');
    const requestId = randomUUID().replaceAll('-', '');
    const run = path.join(privateRoot, 'read/runs', runId);
    const directory = path.join(run, 'queue', requestId);
    for (const folder of [privateRoot, path.join(privateRoot, 'read'), path.join(privateRoot, 'read/runs'),
                          run, path.join(run, 'queue'), directory]) await fs.mkdir(folder, {mode:0o700});
    const created = Date.now() - 10;
    await fs.writeFile(path.join(directory, 'request.json'), JSON.stringify({request_id:requestId,
      operation:'read_workout', workout_id:12345, created_at:new Date(created).toISOString(),
      expires_at:new Date(created + 300000).toISOString()}), {mode:0o600});
    const canonical = 'https://connect.garmin.com/app/workout/12345';
    let current = scenario === 'different' ? 'https://connect.garmin.com/app/' : canonical;
    const actions = [];
    let evaluations = 0;
    const tab = {
      url: async () => current,
      reload: async () => { actions.push('reload'); if (scenario === 'redirect') current='https://sso.garmin.com/'; },
      goto: async value => { actions.push('goto'); current=value; },
      playwright: {evaluate:async () => {
        actions.push('evaluate'); evaluations++;
        if (scenario === 'loading' && evaluations === 1) return {status:'browser_not_ready'};
        return {status:'observed', selected:{name:'SYNTHETIC OFFLINE FIXTURE', sport:null,
          steps:[{name:'Synthetic', duration_seconds:17, targets:null}],
          calendar_evidence:'unknown', watch_evidence:'unknown', api_authentication:'unknown'}};
      }},
    };
    const meta = {run_dir:run, request_id:requestId, request_dir:directory, workout_id:12345};
    let result = await observeQueuedWorkout(meta, tab, {privateRoot});
    if (scenario === 'loading') {
      assert.equal(result.status, 'browser_not_ready');
      result = await observeQueuedWorkout(meta, tab, {privateRoot, navigate:false});
      assert.deepEqual(actions, ['reload','evaluate','evaluate']);
    } else if (scenario === 'same') assert.deepEqual(actions, ['reload','evaluate']);
    else if (scenario === 'different') assert.deepEqual(actions, ['goto','evaluate']);
    else {
      assert.equal(result.status, 'access_denied_or_wrong_page');
      assert.deepEqual(actions, ['reload']);
      await assert.rejects(fs.stat(path.join(directory, 'selected-observation.json')));
      continue;
    }
    assert.equal(result.status, 'observation_saved');
    const selected = JSON.parse(await fs.readFile(path.join(directory, 'selected-observation.json'), 'utf8'));
    assert.equal(selected.steps[0].duration_seconds, 17);
  }
  console.log(JSON.stringify({synthetic_offline_only:true, fake_tab_cases:4, fingerprint_parity:true}));
} finally {
  await fs.rm(root, {recursive:true, force:true}); // This process's anonymous test fixture only.
}
