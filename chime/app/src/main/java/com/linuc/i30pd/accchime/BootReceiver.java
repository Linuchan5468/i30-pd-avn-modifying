package com.linuc.i30pd.accchime;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent) {
        String action=intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action) && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) return;
        ChimeDiagnostics.record(context,"receiver","action="+action+" enabled="+new ChimeStore(context).enabled(),true);
        if (!new ChimeStore(context).enabled()) return;
        context.startService(new Intent(context,ChimeService.class).setAction(
                Intent.ACTION_BOOT_COMPLETED.equals(action)?ChimeService.BOOT:ChimeService.RESUME));
    }
}
