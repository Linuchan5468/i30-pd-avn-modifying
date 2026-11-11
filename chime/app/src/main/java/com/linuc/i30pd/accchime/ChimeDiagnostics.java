package com.linuc.i30pd.accchime;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Process;
import android.os.SystemClock;

/** Private, bounded observation history. Logging never changes playback permission. */
final class ChimeDiagnostics {
    private static final String PREFS="acc_chime_diagnostics", KEY="events";
    private static final int MAX_ENTRIES=128, MAX_BYTES=24576, MAX_DETAIL_CHARS=700;
    private static String processIdentity;

    private ChimeDiagnostics() { }

    private static String clean(String value) {
        if(value==null)return "null";
        StringBuilder safe=new StringBuilder();
        int limit=Math.min(value.length(),MAX_DETAIL_CHARS);
        for(int i=0;i<limit;i++){
            char c=value.charAt(i);
            // Keep one XML-safe line per event; never persist exception stacks or paths.
            safe.append(c<' ' || c=='&' || c=='<' || c=='>' || c=='\"' || c=='\'' ? ' ' : c);
        }
        if(value.length()>limit)safe.append("...");
        return safe.toString();
    }

    private static int utf8Size(String text) {
        try{return text.getBytes("UTF-8").length;}
        catch(java.io.UnsupportedEncodingException impossible){return text.length()*3;}
    }

    /** commit=true persists important transitions before returning to the caller. */
    static synchronized void record(Context context,String event,String detail,boolean commit) {
        try{
            if(processIdentity==null){
                String boot=ChimeStore.bootId();
                processIdentity="pid="+Process.myPid()+" boot="+(boot==null?"unknown":boot.substring(0,Math.min(8,boot.length())));
            }
            String line="w="+System.currentTimeMillis()+" e="+SystemClock.elapsedRealtime()
                    +" u="+SystemClock.uptimeMillis()+" "+processIdentity+" "+clean(event)+" "+clean(detail)+"\n";
            SharedPreferences prefs=context.getSharedPreferences(PREFS,Context.MODE_PRIVATE);
            String previous=prefs.getString(KEY,"");
            // Also bounds reads if an older version or interrupted restore left an oversized value.
            if(previous.length()>MAX_BYTES)previous=previous.substring(previous.length()-MAX_BYTES);
            String combined=previous+line;
            int entries=0;
            for(int i=0;i<combined.length();i++)if(combined.charAt(i)=='\n')entries++;
            while(entries>MAX_ENTRIES || utf8Size(combined)>MAX_BYTES){
                int boundary=combined.indexOf('\n');
                if(boundary<0){combined="";break;}
                combined=combined.substring(boundary+1);entries--;
            }
            SharedPreferences.Editor edit=prefs.edit().putString(KEY,combined);
            if(commit)edit.commit();else edit.apply();
        }catch(RuntimeException ignored){ }
    }

    static synchronized String read(Context context) {
        try{
            String value=context.getSharedPreferences(PREFS,Context.MODE_PRIVATE).getString(KEY,"");
            return value.length()==0?"저장된 상태 기록이 없습니다.":value;
        }catch(RuntimeException ignored){return "상태 기록을 읽지 못했습니다.";}
    }
}
