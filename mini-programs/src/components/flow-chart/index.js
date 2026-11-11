Component({
  properties:{model:{type:Object,value:null,observer:function(model){
    const points=model&&model.points||[];
    const saved=this._group;
    const hit=saved && model?.scope===saved.scope ? points.findIndex(point=>point.key===saved.key && point.end===saved.end) : -1;
    if(hit<0)this._group=null;
    const index=hit<0?Math.max(points.length-1,0):hit;
    this.setData({index,point:points[index]||null});
  }},day:{type:Object,value:null}},
  data:{index:0,point:null},
  methods:{
    pinGroup(index){
      const model=this.properties.model,point=model?.points[index];
      if(!point)return;
      this._group={scope:model.scope,key:point.key,end:point.end};
      this.setData({index,point});
    },
    select(event){
      const points=(this.properties.model||{}).points||[];
      const raw=event.detail&&event.detail.value!=null?event.detail.value:event.currentTarget.dataset.index;
      const i=Math.max(0,Math.min(points.length-1,Math.round(Number(raw))));
      if(points[i]){
        this.pinGroup(i);
        if(this.properties.day)this.triggerEvent('inspect',{date:''});
      }
    },
    inspectDate(event){
      const date=event.detail?.value||event.currentTarget.dataset.date;
      if(date)this.triggerEvent('inspect',{date});
    },
    returnToChart(){
      if(this.properties.day?.listed)this.pinGroup(this.properties.day.group);
      this.triggerEvent('inspect',{date:''});
    }
  }
});
