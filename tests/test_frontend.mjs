import test from 'node:test';
import assert from 'node:assert/strict';
import {intervals,points} from '../custom_components/hive_virtual/frontend/charts.js';
import {nativeRows,nativePayload} from '../custom_components/hive_virtual/frontend/editors.js';

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
