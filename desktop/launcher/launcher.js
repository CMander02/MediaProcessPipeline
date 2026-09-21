const api = window.mppDesktop;
const byId = (id) => document.getElementById(id);

function render(state) {
  byId('title').textContent = state.title;
  byId('message').textContent = state.message;
  byId('project').textContent = state.project || '首次使用时，请选择已经配置好的 MPP 项目。';
  byId('retry').hidden = state.phase !== 'error';
  byId('choose-project').hidden = state.phase !== 'error' || state.owned;
  byId('indicator').classList.toggle('error', state.phase === 'error');
  byId('details').hidden = !state.details;
  byId('log').textContent = state.details || '';
}

function action(id, callback) {
  byId(id).addEventListener('click', async () => {
    byId(id).disabled = true;
    try { await callback(); } catch (error) { byId('message').textContent = error.message; }
    finally { byId(id).disabled = false; }
  });
}

api.onState(render);
api.getState().then(render);
action('retry', api.retry);
action('choose-project', api.chooseProject);
action('open-logs', api.openLogs);
action('quit', api.quit);
