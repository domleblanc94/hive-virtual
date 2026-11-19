import test from 'node:test';
import assert from 'node:assert/strict';
import {intervals,points,usageBuckets} from '../custom_components/hive_virtual/frontend/charts.js';
import {nativeRows,nativePayload} from '../custom_components/hive_virtual/frontend/editors.js';

test('usage charts group daily totals by month and retain gaps', () => {
 const days = {'2026-10-01':{heating_cost:2,currency:'GBP'},'2026-10-03':{heating_cost:1,currency:'GBP'},
   '2026-11-01':{heating_cost:8,currency:'EUR'},'2026-11-09':{heating_cost:0,currency:'GBP'}};
 const weekly = usageBuckets(days,'2026-11-10','week','heating_cost','GBP');
 assert.deepEqual(weekly.slice(-2), [{date:'2026-11-09',value:0,days:1},{date:'2026-11-10',value:null,days:0}]);
 const yearly = usageBuckets(days,'2026-11-10','year','heating_cost','GBP');
 assert.equal(yearly.length,12);
 assert.deepEqual(yearly.slice(-2), [{date:'2026-10',value:3,days:2},{date:'2026-11',value:0,days:1}]);
});

test('timeline retains unknown gaps and clips to the requested period',()=>{
 const states=[{state:'on',last_changed:new Date(500).toISOString()},{state:'off',last_changed:new Date(1500).toISOString()},{state:'unavailable',last_changed:new Date(1700).toISOString()}];
 assert.deepEqual(intervals(states,1000,2000),[{start:1000,end:1500,value:'on'},{start:1500,end:1700,value:'off'},{start:1700,end:2000,value:'unavailable'}]);
 assert.deepEqual(intervals([],1000,2000),[{start:1000,end:2000,value:'unknown'}]);
 assert.equal(points([{state:'on',last_changed:'invalid'}]).length,0);
});
test('native editor preserves other block data and midnight end time',()=>{
 const source={id:'house',name:'House',icon:'mdi:fire',monday:[{from:'22:00:00',to:'24:00:00',data:{temperature:18,area:'Upstairs',custom:'keep me'}}]};
 const rows=nativeRows(source);assert.equal(rows[0].end,'24:00');rows[0].temperature=19;rows[0].days.push(1);
 const payload=nativePayload(rows,source);
 assert.equal(payload.schedule_id,'house');assert.equal(payload.monday[0].data.custom,'keep me');assert.equal(payload.tuesday[0].data.temperature,19);assert.equal(payload.monday[0].to,'24:00:00');assert.equal(payload.sunday.length,0);
});
