'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

test('list filters reserve the longest option width across selections and follow language changes',()=>{
  const events={},documentEvents={};
  const measure={style:{},textContent:'',getBoundingClientRect(){return {width:this.textContent.length*8};}};
  const select=texts=>{const options=texts.map(textContent=>({textContent}));return {style:{},options,selectedOptions:[options[0]],getBoundingClientRect(){return {width:parseInt(this.style.width,10)}}};};
  const surface=select(['All','CLI']),mode=select(['Recent','Project','Working directory']);
  const toolbar={querySelectorAll:()=>[surface,mode],addEventListener(name,fn){events[name]=fn;}};
  const context=vm.createContext({document:{createElement:()=>measure,body:{append(){}},addEventListener(name,fn){documentEvents[name]=fn;}},window:{addEventListener(){}},getComputedStyle:()=>({font:'12px sans-serif',letterSpacing:'normal'}),requestAnimationFrame:fn=>fn()});
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
  vm.runInContext('bindChatListToolbar(toolbar)',Object.assign(context,{toolbar}));
  assert.equal(mode.style.width,'166px');assert.equal(surface.style.width,'54px');
  mode.selectedOptions=[mode.options[1]];events.change();assert.equal(mode.style.width,'166px');
  mode.options.forEach((option,index)=>option.textContent=['最近交互','按项目','按工作目录'][index]);
  documentEvents['bridge-language']();assert.equal(mode.style.width,'70px');
});

test('late options and font loading refresh the reserved width without changing selection',async()=>{
  let optionsChanged;let unit=8;
  const measure={style:{},textContent:'',getBoundingClientRect(){return {width:this.textContent.length*unit};}};
  const selected={textContent:'All'},select={style:{},options:[selected],selectedOptions:[selected]};
  const toolbar={querySelectorAll:()=>[select],addEventListener(){}};
  let fontsLoaded;const ready=new Promise(resolve=>{fontsLoaded=resolve;});
  const context=vm.createContext({document:{fonts:{ready},createElement:()=>measure,body:{append(){}},addEventListener(){}},
    window:{addEventListener(){}},getComputedStyle:()=>({font:'12px sans-serif',letterSpacing:'normal'}),requestAnimationFrame:fn=>fn(),
    MutationObserver:class{constructor(callback){optionsChanged=callback;}observe(){}},toolbar});
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
  vm.runInContext('bindChatListToolbar(toolbar)',context);
  assert.equal(select.style.width,'54px');
  select.options.push({textContent:'Desktop'});optionsChanged?.();
  assert.equal(select.style.width,'86px');
  assert.equal(select.selectedOptions[0],selected);
  unit=10;fontsLoaded();await ready;await Promise.resolve();
  assert.equal(select.style.width,'100px');
});
