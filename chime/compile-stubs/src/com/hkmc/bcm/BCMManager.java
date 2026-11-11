package com.hkmc.bcm;

import android.content.Context;

/** Compilation ABI only. Never include this class in an APK or dex input. */
public class BCMManager {
    public BCMManager(Context context) { throw new UnsupportedOperationException("compile-only ABI"); }
    public void addPowerListener(BCMPowerListener listener) { throw new UnsupportedOperationException("compile-only ABI"); }
    public void removePowerListener(BCMPowerListener listener) { throw new UnsupportedOperationException("compile-only ABI"); }
    public int getEngineStatus() { throw new UnsupportedOperationException("compile-only ABI"); }
}
