const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/../templates/index.html','utf8');
const source=html.slice(html.indexOf('function farmApp()'),html.lastIndexOf('</script>'));
let submitted, alerts=[];
class Form { constructor(){this.values={};} append(k,v){this.values[k]=v;} }
const context=vm.createContext({localStorage:{getItem:()=>null},console,FormData:Form,alert:m=>alerts.push(m),fetch:async(url,opts)=>{submitted=opts.body.values;return{ok:true,json:async()=>({})}}});
vm.runInContext(source,context);
(async()=>{
 const a=vm.runInContext('farmApp()',context);
 const p={id:1,default_region:'2',keywords:Array.from({length:25},(_,i)=>({id:i+1}))};
 assert.equal(a.pagedKeywords(p).length,10);a.setKeywordPage(p,2);assert.equal(a.pagedKeywords(p)[0].id,11);
 a.setKeywordPage(p,3);assert.equal(a.pagedKeywords(p).length,5);
 p.keywords=p.keywords.slice(0,10);assert.equal(a.keywordPage(p),1);
 a.openAddKeyword(p);assert.equal(JSON.stringify(a.newKeyword.geo_regions),'["2"]');
 a.addKeywordRegion('newKeyword','213');a.addKeywordRegion('newKeyword','213');assert.equal(a.newKeyword.geo_regions.length,2);
 a.toggleKeywordRegion('newKeyword','2');assert.equal(JSON.stringify(a.newKeyword.geo_regions),'["213"]');
 a.newKeyword.keywords_text='query';a.newKeyword.geo_regions=['213','2','43','54','65'];a.loadProjects=async()=>{};
 await a.submitAddKeyword();assert.equal(submitted.geo_regions,'["213","2","43","54","65"]');
 a.newKeyword.geo_regions=[];a.showAddKeyword=true;await a.submitAddKeyword();assert.ok(alerts.length);
 assert.ok(html.includes('k in pagedKeywords(p)'));
 console.log('Projects UI: PASS (paging, clamp after deletion, region chips and submission of all 5 codes)');
})().catch(e=>{console.error(e);process.exitCode=1;});
