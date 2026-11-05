const NS='http://www.w3.org/2000/svg';
const COLORS=['#008b91','#dc8700','#9c55b4','#4878c3'];
export function points(states) {
  return (states || []).map(s=>({time:Date.parse(s.last_changed || s.last_updated),value:s.state}))
    .filter(p=>Number.isFinite(p.time)).sort((a,b)=>a.time-b.time);
}
export function intervals(states,start,end) {
  const result=[];let at=start, value='unknown';
  for(const p of points(states)) {
    if(p.time<=start){value=p.value;continue;}
    if(p.time>end)break;
    result.push({start:at,end:p.time,value});at=p.time;value=p.value;
  }
  result.push({start:at,end,value});return result;
}
function svgNode(tag,attrs={},text) {
  const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);
  if(text!==undefined)n.textContent=text;return n;
}
export function activityChart(states,start,end,ariaLabel='Reported heating on, off and unavailable timeline',styles={
  on:['#df8800','On'],off:['#cce4e5','Off'],
}) {
  const svg=svgNode('svg',{viewBox:'0 0 760 82',role:'img','aria-label':ariaLabel});
  for(const item of intervals(states,start,end)) {
    const x=40+(item.start-start)/(end-start)*700,w=(item.end-item.start)/(end-start)*700;
    const [fill,label]=styles[item.value] || ['#aeb5be','Unavailable or no history'];
    const rect=svgNode('rect',{x,y:12,width:Math.max(0,w),height:30,fill});
    rect.append(svgNode('title',{},`${label} · ${new Date(item.start).toLocaleString()} → ${new Date(item.end).toLocaleString()}`));svg.append(rect);
  }
  svg.append(svgNode('text',{x:40,y:68,fill:'currentColor','font-size':13},new Date(start).toLocaleString()));
  svg.append(svgNode('text',{x:740,y:68,fill:'currentColor','font-size':13,'text-anchor':'end'},new Date(end).toLocaleString()));
  return svg;
}
export function temperatureChart(series,start,end) {
  const values=series.flatMap(s=>points(s.states).filter(p=>p.time>=start&&p.time<=end&&p.value!==''&&Number.isFinite(Number(p.value))).map(p=>Number(p.value)));
  if(!values.length){const p=document.createElement('p');p.textContent='No recorded temperatures in this time range.';return p;}
  const min=Math.floor(Math.min(...values))-1,max=Math.ceil(Math.max(...values))+1;
  const svg=svgNode('svg',{viewBox:'0 0 760 250',role:'img','aria-label':'Recorded room temperatures in degrees Celsius'});
  for(let i=0;i<5;i++){
    const y=20+i*45,v=max-(max-min)*i/4;
    svg.append(svgNode('line',{x1:45,x2:740,y1:y,y2:y,stroke:'currentColor',opacity:0.12}));
    svg.append(svgNode('text',{x:39,y:y+4,'text-anchor':'end',fill:'currentColor','font-size':12},v.toFixed(1)));
  }
  series.forEach((s,index)=>{
    let path='',active=false;
    for(const p of points(s.states)){
      if(p.time<start||p.time>end)continue;
      const value=Number(p.value);
      if(!Number.isFinite(value)||p.value===''){active=false;continue;}
      const x=45+(p.time-start)/(end-start)*695,y=20+(max-value)/(max-min)*180;
      path+=`${active?'L':'M'}${x.toFixed(1)},${y.toFixed(1)} `;active=true;
    }
    svg.append(svgNode('path',{d:path,fill:'none',stroke:COLORS[index%COLORS.length],'stroke-width':2.5}));
    svg.append(svgNode('text',{x:45+index*175,y:240,fill:COLORS[index%COLORS.length],'font-size':13},s.name));
  });
  svg.append(svgNode('text',{x:45,y:221,fill:'currentColor','font-size':12},new Date(start).toLocaleString()));
  svg.append(svgNode('text',{x:740,y:221,fill:'currentColor','font-size':12,'text-anchor':'end'},new Date(end).toLocaleString()));return svg;
}
