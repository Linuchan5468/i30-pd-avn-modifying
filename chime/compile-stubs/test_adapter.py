"""Offline JVM checks with fake Android/OEM objects; never installs or plays audio.

Usage: python compile-stubs/test_adapter.py --java PATH_TO_JAVA_EXE [--evidence PATH]
Fixtures are generated in a temporary directory and never enter application inputs.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


FIXTURES = {
    "com/linuc/i30pd/accchime/ChimeDiagnostics.java": "package com.linuc.i30pd.accchime; import android.content.Context; final class ChimeDiagnostics { static void record(Context c,String e,String d,boolean critical) {} }",
    "android/content/BroadcastReceiver.java": "package android.content; public class BroadcastReceiver {}",
    "android/content/IntentFilter.java": "package android.content; public class IntentFilter { public IntentFilter(String a) {} }",
    "android/content/Intent.java": """package android.content;
import java.util.HashMap;
public class Intent {
    private String action; private HashMap<String,Object> extras=new HashMap<String,Object>();
    public Intent(String a){action=a;} public String getAction(){return action;}
    public boolean hasExtra(String k){return extras.containsKey(k);}
    public Intent putExtra(String k,int v){extras.put(k,Integer.valueOf(v));return this;}
    public Intent putExtra(String k,long v){extras.put(k,Long.valueOf(v));return this;}
    public int getIntExtra(String k,int d){Object v=extras.get(k);return v instanceof Integer?(Integer)v:d;}
    public long getLongExtra(String k,long d){Object v=extras.get(k);return v instanceof Long?(Long)v:d;}
}""",
    "android/content/Context.java": """package android.content;
public class Context {
    public Intent sticky; public boolean stickyThrows;
    public Context getApplicationContext(){return this;}
    public Intent registerReceiver(BroadcastReceiver r,IntentFilter f){
        if(stickyThrows)throw new SecurityException("fixture");return sticky;
    }
}""",
    "android/os/Looper.java": "package android.os; public class Looper { static final Looper MAIN=new Looper(); public static Looper myLooper(){return MAIN;} }",
    "android/os/SystemClock.java": "package android.os; public class SystemClock { public static long now=10000L; public static long uptimeMillis(){return now;} public static long elapsedRealtime(){return now;} }",
    "android/os/Handler.java": """package android.os;
import java.util.ArrayList;
public class Handler {
    private static class Pending {Runnable r;long due;Pending(Runnable r,long d){this.r=r;due=d;}}
    private ArrayList<Pending> queue=new ArrayList<Pending>();
    public Looper getLooper(){return Looper.MAIN;}
    public boolean post(Runnable r){return postDelayed(r,0);}
    public boolean postDelayed(Runnable r,long delay){queue.add(new Pending(r,SystemClock.now+delay));return true;}
    public void removeCallbacks(Runnable r){for(int i=queue.size()-1;i>=0;i--)if(queue.get(i).r==r)queue.remove(i);}
    public void drain(){int guard=0;while(true){
        int next=-1;long due=Long.MAX_VALUE;
        for(int i=0;i<queue.size();i++)if(queue.get(i).due<=SystemClock.now&&queue.get(i).due<due){next=i;due=queue.get(i).due;}
        if(next<0)return;if(++guard>1000)throw new AssertionError("fixture infinite queue");
        queue.remove(next).r.run();
    }}
    public void advance(long ms){SystemClock.now+=ms;drain();}
}""",
    "android/os/IInterface.java": "package android.os; public interface IInterface { IBinder asBinder(); }",
    "android/os/IBinder.java": """package android.os;
public interface IBinder { interface DeathRecipient {void binderDied();}
    boolean isBinderAlive();void linkToDeath(DeathRecipient d,int flags)throws Exception;
    boolean unlinkToDeath(DeathRecipient d,int flags);
}""",
    "com/hkmc/bcm/BCMManager.java": """package com.hkmc.bcm;
import android.content.Context;import android.os.*;
public class BCMManager {
    public static BCMManager latest;public static int replay=5,removes;
    public static boolean failRead,failAdd,missingService;
    public static class Transport implements IBinder,IInterface {
        public boolean alive=true;DeathRecipient recipient;
        public IBinder asBinder(){return this;}public boolean isBinderAlive(){return alive;}
        public void linkToDeath(DeathRecipient d,int flags){recipient=d;}
        public boolean unlinkToDeath(DeathRecipient d,int flags){recipient=null;return true;}
        public void die(){alive=false;if(recipient!=null)recipient.binderDied();}
    }
    public static Transport transport=new Transport();
    private final IInterface mService;private BCMPowerListener listener;private int cached=-1;
    public BCMManager(Context c){latest=this;mService=missingService?null:transport;}
    public void addPowerListener(final BCMPowerListener l){
        listener=l;l.onACCChanged(cached);if(failAdd)throw new IllegalStateException("fixture registration");
        if(replay>=0){cached=replay;new Handler().post(new Runnable(){public void run(){l.onACCChanged(cached);}});}
    }
    public void removePowerListener(BCMPowerListener l){removes++;if(listener==l)listener=null;}
    public int getEngineStatus(){if(failRead)throw new IllegalStateException("fixture getter");return cached;}
    public void emit(int state){cached=state;if(listener!=null)listener.onACCChanged(state);}
    public void cacheOnly(int state){cached=state;}
    public void replayNow(){if(listener!=null)listener.onACCChanged(cached);}
    public static void reset(){replay=5;removes=0;failRead=false;failAdd=false;missingService=false;transport=new Transport();}
}""",
    "Harness.java": """import android.content.*;import android.os.*;
import com.hkmc.bcm.BCMManager;import com.linuc.i30pd.accchime.OemAccSource;
import java.util.ArrayList;
public class Harness implements OemAccSource.Listener {
    ArrayList<OemAccSource.Event> events=new ArrayList<OemAccSource.Event>();
    boolean healthy;Context context=new Context();Handler handler=new Handler();
    OemAccSource source=new OemAccSource(context,handler,this);
    public void onAccEvent(OemAccSource.Event e){events.add(e);}
    public void onSourceHealth(boolean h,String detail){healthy=h;}
    static int checks;
    static void check(boolean ok,String name){if(!ok)throw new AssertionError(name);checks++;}
    static Harness fresh(){BCMManager.reset();return new Harness();}
    OemAccSource.Event last(){return events.get(events.size()-1);}
    void start(){source.start();handler.drain();BCMManager.latest.replayNow();handler.drain();}
    void emit(int s){BCMManager.latest.emit(s);handler.drain();}
    void close(){source.stop();handler.drain();}
    static Intent sticky(int status,long timestamp){return new Intent("com.hkmc.intent.action.ACTION_ACC_STATUS_CHANGED")
        .putExtra("android.ibox.mode.EXTRA_ACC_STATUS",status)
        .putExtra("android.ibox.mode.EXTRA_PREVIOUS_ACC_STATUS",255)
        .putExtra("android.ibox.mode.TIMESTAMP",timestamp);}
    public static void main(String[] args){
        Harness h=fresh();h.start();
        check(h.events.get(0).rawState==-1&&h.events.get(0).initialSnapshot,"unknown replay");
        check(h.last().rawState==5&&h.last().initialSnapshot,"first recognized async replay");
        check(h.healthy,"known callback source healthy");
        check(h.source.isCurrentOn(),"last moment ON guard accepts live healthy ON");
        h.emit(0);check(!h.last().initialSnapshot&&h.last().rawState==0,"later OFF is observed");
        check(!h.source.isCurrentOn(),"last moment ON guard rejects OFF");
        h.emit(5);check(!h.last().initialSnapshot&&h.last().rawState==5,"later ON is observed");
        h.emit(-1);check(!h.healthy&&h.last().rawState==-1,"UNKNOWN remains UNKNOWN and unhealthy");
        int before=h.events.size();BCMManager.failRead=true;h.handler.advance(3000);
        check(!h.healthy&&h.events.size()==before,"getter failure emits no OFF or state");h.close();

        h=fresh();h.start();before=h.events.size();BCMManager.failRead=true;
        check(!h.source.isCurrentOn()&&h.events.size()==before,"guard getter failure has no synthetic edge");h.close();

        h=fresh();h.start();before=h.events.size();BCMManager.latest.emit(5);BCMManager.latest.cacheOnly(0);h.handler.drain();
        check(!h.healthy&&h.events.size()==before,"queued stale ON against cached OFF is revoked and dropped");
        h.handler.advance(3000);check(h.events.size()==before,"getter cache does not invent OFF edge");h.close();

        h=fresh();h.start();before=h.events.size();BCMManager.latest.emit(0);h.source.stop();h.handler.drain();
        check(h.events.size()==before,"queued old callbacks discarded after stop");
        h.source.start();h.handler.drain();BCMManager.latest.replayNow();h.handler.drain();
        check(h.last().rawState==5&&h.last().initialSnapshot,"restart is initial snapshot");h.close();

        h=fresh();h.context.sticky=sticky(0,SystemClock.now-500);h.start();
        check(h.last().sourceTimestampMs==SystemClock.now-500&&h.last().powerStatus==0
            &&h.last().previousPowerStatus==255&&"uptimeMillis".equals(h.last().sourceTimestampClock),"matching explicit namespaces");
        h.context.sticky=sticky(255,SystemClock.now-100);h.emit(5);
        check(h.last().sourceTimestampMs==-1&&h.last().powerStatus==-1,"power OFF255 not BCM ON5");
        h.context.sticky=sticky(0,SystemClock.now+1);h.emit(5);
        check(h.last().sourceTimestampMs==-1,"future timestamp rejected");
        h.context.stickyThrows=true;h.emit(5);
        check(h.healthy&&h.last().sourceTimestampMs==-1,"optional metadata failure preserves BCM health");h.close();

        h=fresh();h.start();before=h.events.size();BCMManager.transport.die();h.handler.drain();
        check(!h.healthy&&h.events.size()==before,"Binder death emits no OFF or state");h.close();

        h=fresh();BCMManager.failAdd=true;h.source.start();h.handler.drain();
        check(!h.healthy&&BCMManager.removes==1&&h.events.isEmpty(),"partial registration cleaned and rejected");h.close();

        h=fresh();BCMManager.missingService=true;h.source.start();h.handler.drain();
        check(!h.healthy&&h.events.isEmpty(),"absent Binder unavailable without OFF");h.close();

        h=fresh();BCMManager.replay=-1;h.start();before=h.events.size();h.handler.advance(15000);
        check(!h.healthy&&h.events.size()==before&&BCMManager.removes==1,"unknown timeout is unavailable not OFF");h.close();
        System.out.println("PASS "+checks+" adapter assertions");
    }
}""",
    "MissingOemHarness.java": """import android.content.*;import android.os.*;
import com.linuc.i30pd.accchime.OemAccSource;
public class MissingOemHarness implements OemAccSource.Listener {
    int events;boolean healthy;public void onAccEvent(OemAccSource.Event e){events++;}
    public void onSourceHealth(boolean h,String d){healthy=h;}
    public static void main(String[] a){MissingOemHarness h=new MissingOemHarness();
        Handler handler=new Handler();OemAccSource source=new OemAccSource(new Context(),handler,h);
        source.start();handler.drain();if(h.healthy||h.events!=0)throw new AssertionError("missing OEM classes");
        source.stop();System.out.println("PASS missing OEM classes degrade without crash or OFF");
    }
}""",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    java = args.java.resolve()
    javac = java.with_name("javac.exe" if java.suffix.lower() == ".exe" else "javac")
    root = Path(__file__).resolve().parents[1]
    output = []
    with tempfile.TemporaryDirectory(prefix="i30pd-acc-adapter-") as temp:
        work = Path(temp)
        sources = []
        for name, content in FIXTURES.items():
            path = work / "src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            sources.append(path)
        sources += [root / "compile-stubs/src/com/hkmc/bcm/BCMPowerListener.java",
                    root / "app/src/main/java/com/linuc/i30pd/accchime/OemAccSource.java"]
        classes = work / "classes"
        classes.mkdir()
        def run(command):
            result = subprocess.run([str(p) for p in command], capture_output=True, text=True)
            if result.returncode:
                raise SystemExit(result.stdout + result.stderr)
            return result.stdout.strip()
        run([javac, "-source", "8", "-target", "8", "-Xlint:-options", "-d", classes, *sources])
        output.append(run([java, "-cp", classes, "Harness"]))
        no_oem = work / "without-oem"
        shutil.copytree(classes, no_oem)
        shutil.rmtree(no_oem / "com/hkmc")
        output.append(run([java, "-cp", no_oem, "MissingOemHarness"]))
    evidence = {"schema": 1, "status": "passed", "method": "Offline desktop JVM with generated fake Android/OEM classes",
                "results": output, "not_validated": ["Dalvik verifier behavior", "real OEM service/permissions", "vehicle signals", "audio"],
                "scope": "No APK install, vehicle, emulator, ADB, audio playback or network operation."}
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("\n".join(output))


if __name__ == "__main__":
    main()
