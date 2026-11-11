Component({
  properties:{model:{type:Object,value:null,observer:function(model){
    const points=model&&model.points||[];
    const index=Math.max(points.length-1,0);
    this.setData({index,point:points[index]||null});
  }},day:{type:Object,value:null}},
  data:{index:0,point:null},
  methods:{
    select(event){
      const points=(this.properties.model||{}).points||[];
      const raw=event.detail&&event.detail.value!=null?event.detail.value:event.currentTarget.dataset.index;
      const i=Math.max(0,Math.min(points.length-1,Math.round(Number(raw))));
      if(points[i]){
        this.setData({index:i,point:points[i]});
        if(this.properties.day)this.triggerEvent('inspect',{date:''});
      }
    },
    inspectDate(event){
      const date=event.detail?.value||event.currentTarget.dataset.date;
      if(date)this.triggerEvent('inspect',{date});
    },
    returnToChart(){this.triggerEvent('inspect',{date:''});}
  }
});
