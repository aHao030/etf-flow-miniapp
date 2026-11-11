// Astryx 0.7.13 light tokens adapted to native rpx (750 design units / screen).
// Theme colors match the graphite web shell and the Chinese market flow convention.
const tokens = {
  'color-background-body':'#f7f7f7', 'color-background-surface':'#ffffff',
  'color-background-muted':'#f1f1f1', 'color-text-primary':'#171717',
  'color-text-secondary':'#565656', 'color-border':'#e1e1e1',
  'color-border-emphasized':'#c8c8c8', 'color-track':'#d6d6d6',
  'color-accent':'#171717', 'color-on-accent':'#ffffff',
  'market-inflow':'#c63536', 'market-outflow':'#167451',
  'spacing-0-5':'4rpx', 'spacing-1':'8rpx','spacing-1-5':'12rpx',
  'spacing-2':'16rpx', 'spacing-3':'24rpx','spacing-4':'32rpx',
  'spacing-5':'40rpx','spacing-6':'48rpx','spacing-8':'64rpx',
  'radius-inner':'8rpx','radius-element':'16rpx','radius-container':'24rpx','radius-full':'9999rpx',
  'font-size-sm':'24rpx','font-size-base':'28rpx','font-size-lg':'34rpx',
  'font-size-xl':'40rpx','font-size-2xl':'48rpx','font-size-3xl':'58rpx',
  'size-element-lg':'72rpx','touch-min':'44px'
};

// Substitute before platform compilation, including native slider/host chrome
// attributes. They take literal colors, and component scope varies by host.
export function resolveNativeTheme(source){
  return source.replace(/var\(--([a-z0-9-]+)\)/g,(_,key)=>{
    if(!Object.hasOwn(tokens,key))throw Error('未定义的小程序主题令牌 '+key);
    return tokens[key];
  });
}
