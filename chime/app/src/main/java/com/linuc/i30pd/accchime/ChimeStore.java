package com.linuc.i30pd.accchime;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.BufferedReader;
import java.io.FileReader;

final class ChimeStore {
    final SharedPreferences prefs;
    ChimeStore(Context context) { prefs=context.getSharedPreferences("acc_chime",Context.MODE_PRIVATE); }
    boolean enabled() { return prefs.getBoolean("enabled",false); }
    boolean enable(boolean value) { return prefs.edit().putBoolean("enabled",value).commit(); }
    ChimePolicy.State load() {
        String boot=prefs.getString("boot_id",null);
        if (boot==null) return null;
        return new ChimePolicy.State(boot,prefs.getBoolean("armed",false),prefs.getLong("off_since",-1),
                prefs.getLong("last_elapsed",0),prefs.getLong("sequence",0));
    }
    boolean save(ChimePolicy.State state) {
        return prefs.edit().putString("boot_id",state.bootId).putBoolean("armed",state.armed)
                .putLong("off_since",state.offSince).putLong("last_elapsed",state.lastElapsed)
                .putLong("sequence",state.sequence).commit();
    }
    void status(String value) { prefs.edit().putString("status",value).apply(); }
    void selected(int index) { prefs.edit().putInt("last_clip",index+1).apply(); }
    static String bootId() {
        try {
            BufferedReader reader=new BufferedReader(new FileReader("/proc/sys/kernel/random/boot_id"));
            try { String value=reader.readLine(); if(value!=null && value.trim().length()>0)return value.trim(); }
            finally { reader.close(); }
        } catch(Exception ignored) { }
        return null; // fail closed rather than invent a new boot on each process start
    }
}
