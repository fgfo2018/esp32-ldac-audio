"""Windowed input peak; callbacks and sampling both run on the asyncio loop."""
import math

class PeakMeter:
    def __init__(self):self.peak=0.0
    def push(self,value):
        if math.isfinite(value):self.peak=max(self.peak,min(1.0,max(0.0,value)))
    def take(self):
        value=self.peak;self.peak=0.0
        return value
