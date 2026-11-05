const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/../templates/index.html','utf8');
const source=html.slice(html.indexOf('function farmApp()'),html.lastIndexOf('</script>'));
let intervals=[],hits=[],errors=[];
const context=vm.createContext({localStorage:{getItem:()=>null},
  setInterval:(fn,ms)=>{intervals.push(ms);return intervals.length;},clearInterval:()=>{},setTimeout:()=>{},
  alert:e=>errors.push(e),console,fetch:async url=>{hits.push(url);return {ok:false,status:401,json:async()=>({detail:'Unauthorized'})};}});
vm.runInContext(source,context);
(async()=>{
  const a=vm.runInContext('farmApp()',context);
  await a.init();await a.init();
  assert.equal(intervals.filter(ms=>ms===5000).length,1);
  assert.equal(html.includes('x-init="init()"'),false);
  hits=[];await a.refreshDashboard();
  for(const path of ['/api/status','/api/projects','/api/logs?limit=40'])assert.ok(hits.includes(path));
  assert.ok(a.status.profiles);
  await a.saveSettings();assert.equal(a.saveStatusMsg,'');assert.equal(errors.length,1);
  assert.ok(html.includes('status.attempts_today'));assert.ok(html.includes('settings.keyword_min_interval_sec'));
  console.log('Frontend dispatch checks: PASS (single init, live counters, logs/projects refresh, error handling)');
})().catch(e=>{console.error(e);process.exitCode=1;});
