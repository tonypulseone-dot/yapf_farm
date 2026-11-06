const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/../templates/index.html','utf8'),source=html.slice(html.indexOf('function farmApp()'),html.lastIndexOf('</script>'));
let fetchImpl,alerts=[];
const c=vm.createContext({localStorage:{getItem:()=>null},console,alert:x=>alerts.push(x),setInterval:()=>1,clearInterval:()=>{},fetch:(...args)=>fetchImpl(...args)});
vm.runInContext(source,c);
(async()=>{
 const a=vm.runInContext('farmApp()',c);a.selectedPositionProjectId='1';let resolveOld=[];
 fetchImpl=async url=>{
  const u=new URL(url,'http://test'),id=u.searchParams.get('project_id');
  const response={ok:true,json:async()=>url.includes('/timeline')?{dates:[],rows:[{project_id:id}]}:{total_domains:id}};
  if(id==='1')await new Promise(r=>resolveOld.push(r));return response;
 };
 const old=a.loadPositionsData();a.selectedPositionProjectId='2';await a.loadPositionsData();resolveOld.forEach(r=>r());await old;
 assert.equal(a.positionTimeline.rows[0].project_id,'2','late reply cannot replace selected project');
 fetchImpl=async()=>({ok:true,json:async()=>({project_id:2,is_running:false,current:0,jobs:[{project_id:1,is_running:true}]})});
 a.loadPositionsData=async()=>{};await a.checkPositionsProgress();assert.equal(a.posProgress.is_running,false);assert.equal(a.positionJobs.length,1);
 let posts=0,release;
 fetchImpl=async(url,opts)=>{posts++;await new Promise(r=>release=r);return{ok:true,json:async()=>({status:'ok'})};};
 a.checkPositionsProgress=async()=>{};
 const first=a.runPositionsCheck();await a.runPositionsCheck();assert.equal(posts,1);release();await first;
 assert.equal(a.rankCellValue(null),'—');assert.equal(a.rankCellValue({position:0}),'>100');
 assert.equal(a.rankCellDelta({direction:'up',diff:3}),'▲3');
 assert.equal(a.rankCellDelta({direction:'left',diff:null}),'▼');
 console.log('Position UI: PASS (stale responses, project-scoped progress, double submit, truthful daily cells)');
})().catch(e=>{console.error(e);process.exitCode=1;});
