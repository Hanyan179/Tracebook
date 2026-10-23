const {test}=require('node:test');
const assert=require('node:assert/strict');
const demo=require('../assets/landing.js');
test('capture is a reversible three-state web-only demo',()=>{
  let state='idle';state=demo.nextCapture(state);assert.equal(state,'recording');
  state=demo.nextCapture(state);assert.equal(state,'saved');
  state=demo.nextCapture(state);assert.equal(state,'idle');
});
test('memory uses real confirm/deactivate/restore direction, never unconfirm',()=>{
  let state='candidate';state=demo.nextMemory(state);assert.equal(state,'confirmed');
  state=demo.nextMemory(state);assert.equal(state,'inactive');
  state=demo.nextMemory(state);assert.equal(state,'confirmed');
  for(let i=0;i<20;i++){state=demo.nextMemory(state);assert.notEqual(state,'candidate');}
});
test('sticky stages match measured progress thresholds',()=>{
  for(const [value,expected] of [[0,0],[.499,0],[.5,1],[.819,1],[.82,2],[1,2]])assert.equal(demo.stepAt(value),expected);
});
test('source filters preserve exact declared source kinds',()=>{
  for(const kind of ['note','input','document'])assert.equal(demo.sourceVisible('all',kind),true);
  assert.equal(demo.sourceVisible('note','note'),true);assert.equal(demo.sourceVisible('note','input'),false);
});
