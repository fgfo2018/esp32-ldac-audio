"""Conservative adaptive bitrate with hysteresis; lower index means better quality."""
class AdaptiveQuality:
    def __init__(self,requested=2):
        self.current=requested;self.last_change=0.;self.last_drops=0
    def choose(self,requested,enabled,baud,rssi,drops,backlog,now):
        ceiling=max(requested,0 if baud>=1500000 else 1)
        if not enabled:
            self.current=ceiling
        elif now-self.last_change>=10:
            congested=drops>self.last_drops or backlog>8192 or (rssi is not None and rssi<0)
            if congested:self.current=min(2,max(self.current,ceiling)+1);self.last_change=now
            elif now-self.last_change>=30 and backlog<2048 and (rssi is None or rssi>=0):
                self.current=max(ceiling,self.current-1);self.last_change=now
        self.last_drops=drops
        self.current=max(ceiling,self.current)
        return self.current
