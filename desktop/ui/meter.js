/* Live input level: 30 Hz samples, display refresh driven animation. */
class AudioMeter {
 constructor(){this.element=null;this.enabled=false;this.value=0;this.target=0;this.received=-Infinity;this.frame=null;this.previous=0}
 attach(element,enabled){
  this.element=element;this.enabled=enabled;
  if(!enabled){this.value=0;this.target=0;this.received=-Infinity}
  if(this.frame!==null)cancelAnimationFrame(this.frame);
  this.frame=null;this.previous=performance.now();
  if(element){this.paint();if(enabled)this.frame=requestAnimationFrame(t=>this.tick(t))}
 }
 update(peak){
  if(!this.enabled)return;
  peak=Number.isFinite(peak)?Math.max(0,Math.min(1,peak)):0;
  // A logarithmic scale makes quiet passages readable without changing audio.
  this.target=peak>0?Math.max(0,(20*Math.log10(peak)+60)/60):0;
  this.received=performance.now();
 }
 tick(now){
  this.frame=null;
  if(!this.element||!this.enabled)return;
  const dt=Math.max(0,Math.min(.1,(now-this.previous)/1000));this.previous=now;
  const target=now-this.received>250?0:this.target;
  this.value+=(target-this.value)*(1-Math.exp(-dt/(target>this.value ? .02 : .14)));
  if(this.value<.001)this.value=0;
  this.paint();this.frame=requestAnimationFrame(t=>this.tick(t));
 }
 paint(){this.element.style.transform=`scaleX(${this.value})`;this.element.parentElement.setAttribute('aria-valuenow',String(Math.round(this.value*100)))}
}
