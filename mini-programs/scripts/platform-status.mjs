import {fileURLToPath} from 'node:url';
import {recordPlatform,readPlatforms} from './platform-ledger.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
function display(status){
  const modes={snapshot:'离线体验包',local:'同机联网测试包',live:'手机联网候选包'};
  const states={in_review:'审核中',approved:'审核已通过',rejected:'审核被驳回'};
  console.log('ETF 资金观察 · 本机交付');
  for(const [mode,label] of Object.entries(modes)){
    console.log(label+'　'+(status.localCandidates[mode]?.id||'尚未生成'));
  }
  console.log('\n平台后台确认记录');
  for(const [key,label] of [['wechat','微信'],['xiaohongshu','小红书']]){
    const {active,trial,review}=status.platformRecords[key];
    console.log(label+'　线上 '+(active||'尚无确认记录')+'　体验 '+(trial||'尚无记录'));
    if(review)console.log('　'+states[review.state]+'　'+review.id);
  }
}
try{
  const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
  const args=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if(process.argv[2]==='--display'){
    display(args);
  } else {
    const result=args.action==='status'?await readPlatforms(root):await recordPlatform(root,args);
    console.log(JSON.stringify(result));
  }
}catch(error){console.error(error.message);process.exitCode=1;}
