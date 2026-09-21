const http = require('node:http');
const port = Number(process.argv[2]);
const mode = process.argv[3];
if (mode === 'crash') process.exit(7);
console.log(`MPP_DESKTOP_PID=${process.pid}`);
let server;
if (mode !== 'timeout') {
  server = http.createServer((request, response) => {
    if (request.url === '/health') {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ status: 'healthy', app: 'mpp', pid: mode === 'wrong-pid' ? process.pid + 1 : process.pid, version: '0.0.0' }));
    } else {
      response.setHeader('Content-Type', 'text/html');
      response.end('<html><div id="root">MPP fixture</div></html>');
    }
  }).listen(port, 'localhost');
}
process.stdin.resume();
process.stdin.once('end', () => {
  if (mode === 'stubborn') return;
  if (server) server.close(() => process.exit(0));
  else process.exit(0);
});
if (mode === 'stubborn') {
  const child = require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    windowsHide: true, stdio: 'ignore',
  });
  console.log(`MPP_FIXTURE_CHILD=${child.pid}`);
  setInterval(() => {}, 1000);
}
