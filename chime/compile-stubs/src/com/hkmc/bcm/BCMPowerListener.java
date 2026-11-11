package com.hkmc.bcm;

/** Compilation ABI only. Exact public V141 callback spellings, including Enging. */
public class BCMPowerListener {
    public BCMPowerListener() { }
    public void onACCChanged(int status) { }
    public void onIgnitionChanged(boolean on) { }
    public void onRemoteEngingStatusChanged(int state) { }
    public void onFuelWarnStatusChanged(boolean on) { }
}
