// The Astryx analytics dashboard supplies the page frame and spacing. Financial
// views keep their DOM bindings so a fund drill-down never discards the selected range.
import {createElement as h} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {parseFragment, serialize} from 'parse5';
import {Theme} from '@astryxdesign/core/theme';
import {themes,defaultThemeSlug} from '../astryx-themes.js';
import {InternationalizationProvider} from '@astryxdesign/core/i18n';
import chinese from '@astryxdesign/core/locales/zh-CN.generated.js';
import {AppShell} from '@astryxdesign/core/AppShell';
import {TopNav, TopNavHeading} from '@astryxdesign/core/TopNav';
import {Layout, LayoutContent} from '@astryxdesign/core/Layout';
import {HStack} from '@astryxdesign/core/HStack';
import {VStack} from '@astryxdesign/core/VStack';
import {Section} from '@astryxdesign/core/Section';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {Text} from '@astryxdesign/core/Text';

const workspaceSlot = '__ETF_AUTHORED_WORKSPACE__';
const sectionSlot = '__ETF_AUTHORED_SECTION__';

function render(component) {
  return renderToStaticMarkup(h(InternationalizationProvider, {locale:'zh-CN', messages:{'zh-CN':chinese}},
    h(Theme, {theme:themes[defaultThemeSlug], mode:'light'}, component)));
}

function navigation() {
  const views = h(HStack, {gap:1, className:'market-views', 'aria-label':'观察视图', role:'group'},
    h(Button, {id:'broad-tab', label:'宽基指数', variant:'ghost', size:'sm', 'aria-pressed':true}),
    h(Button, {id:'stock-tab', label:'个股观察', variant:'ghost', size:'sm', 'aria-pressed':false}));
  const unit = h('label', {htmlFor:'money-unit', className:'money-unit-control'},
    h(Text, {type:'supporting', as:'span'}, '资金单位'),
    h('select', {id:'money-unit', 'aria-label':'资金显示单位', defaultValue:'auto'},
      h('option',{value:'auto'},'自动换算'),
      h('option',{value:'wan'},'万元'), h('option',{value:'yi'},'亿元')));
  return h(TopNav, {
    label:'ETF 资金观察',
    heading:h(TopNavHeading,{heading:'ETF 资金观察',headingHref:'/'}),
    startContent:views,
    endContent:h(HStack,{gap:3,vAlign:'center',className:'market-tools'},unit,
      h(Button,{id:'refresh',label:'刷新数据',variant:'secondary',size:'sm'}))
  });
}

const designActions=new Set(['broad-apply','history-apply','broad-open-history','broad-return-index','price-history-open','price-history-apply','watch-toggle']);
function buildContent(source) {
  const fragment=parseFragment(source);
  const sectionFrame=renderToStaticMarkup(h(Section,{padding:0},sectionSlot));
  function convert(node) {
    if(!node.childNodes)return node;
    node.childNodes=node.childNodes.map(convert);
    const attrs=Object.fromEntries((node.attrs||[]).map(({name,value})=>[name,value]));
    const plain=node.childNodes.length===1&&node.childNodes[0].nodeName==='#text';
    let component;
    if(plain && /^h[123]$/.test(node.tagName)) {
      component=h(Heading,{level:Number(node.tagName[1]),weight:'semibold',id:attrs.id,
        tabIndex:attrs.tabindex===undefined?undefined:Number(attrs.tabindex)},node.childNodes[0].value);
    } else if(plain && node.tagName==='button' && designActions.has(attrs.id)) {
      component=h(Button,{id:attrs.id,label:node.childNodes[0].value,size:'sm',type:'button',
        variant:attrs.id.endsWith('-apply')?'primary':'secondary',disabled:'disabled' in attrs,
        hidden:'hidden' in attrs,'aria-label':attrs['aria-label']});
    }
    if(component)return parseFragment(renderToStaticMarkup(component)).childNodes[0];
    if(node.tagName==='section' && attrs.class?.split(' ').includes('panel')){
      const wrapper=parseFragment(sectionFrame.replace(sectionSlot,()=>serialize(node)));
      node.childNodes=wrapper.childNodes;
    }
    return node;
  }
  return serialize(convert(fragment));
}

export function renderDashboard(document) {
  const marker=/<template id="market-workspace">([\s\S]*?)<\/template>/;
  const match=marker.exec(document);
  if(!match)throw Error('Missing authored financial workspace');
  const frame=render(h(AppShell,{topNav:navigation(),height:'auto',variant:'section',mobileNav:false},
    h(Layout,{height:'auto',content:h(LayoutContent,{padding:6},
      h(VStack,{gap:6,className:'market-page'},workspaceSlot))})));
  return document.replace(marker,()=>frame.replace(workspaceSlot,()=>buildContent(match[1])));
}

