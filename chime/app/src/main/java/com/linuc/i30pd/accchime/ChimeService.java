package com.linuc.i30pd.accchime;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.res.AssetFileDescriptor;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.IBinder;
import android.os.SystemClock;
import android.util.Log;
import java.util.Random;

/** Ordinary-UID companion. No MICOM writes, volume changes or firmware changes. */
public final class ChimeService extends Service implements OemAccSource.Listener {
    public static final String ENABLE="com.linuc.i30pd.accchime.ENABLE";
    public static final String BOOT="com.linuc.i30pd.accchime.BOOT";
    public static final String RESUME="com.linuc.i30pd.accchime.RESUME";
    public static final String PREVIEW="com.linuc.i30pd.accchime.PREVIEW";
    public static final String CANCEL="com.linuc.i30pd.accchime.CANCEL";
    private static final String TAG="I30AccChime";
    private static final String[] CLIPS={"chime_01.wav","chime_02.wav"};
    private final Handler handler=new Handler();
    private final Random random=new Random();
    private ChimeStore store;
    private ChimePolicy policy;
    private OemAccSource source;
    private AudioManager audio;
    private MediaPlayer player;
    private AudioManager.OnAudioFocusChangeListener focus;
    private Runnable pendingRetry;
    private String lastAudioGuard;
    private long generation, playbackToken, manualDeadline;
    private boolean autoPlayback, started, monitoring, activeRequest, waitingForAudio;
    private final Runnable clockTick=new Runnable(){public void run(){
        if(policy!=null && policy.tick(SystemClock.elapsedRealtime())){
            diagnostic("policy_rearmed",policyState(),true);
            if(!store.save(policy.snapshot()))cancelPlayback("ACC 상태를 저장하지 못했습니다.");
        }
        if(autoPlayback && (source==null || !source.isCurrentOn()))
            cancelPlayback("ACC 상태 변경으로 재생을 취소했습니다.");
        else if(started && !audioAllowed())cancelPlayback("음량·통화 상태 변경으로 재생을 중지했습니다.");
        if(monitoring || activeRequest)handler.postDelayed(this,500);
    }};
    @Override public void onCreate(){super.onCreate();store=new ChimeStore(this);audio=(AudioManager)getSystemService(AUDIO_SERVICE);
        diagnostic("service_create","enabled="+store.enabled()+" saved="+savedState(store.load()),true);}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        String action=intent==null?RESUME:intent.getAction();
        diagnostic("service_start","action="+action+" nullIntent="+(intent==null)+" flags="+flags
                +" startId="+startId+" enabled="+store.enabled()+" monitoring="+monitoring,true);
        foreground();
        if(CANCEL.equals(action)){cancelPlayback("재생을 중지했습니다.");if(!store.enabled())stopSelf();return START_NOT_STICKY;}
        if(store.enabled() && !monitoring)beginMonitoring(BOOT.equals(action),ENABLE.equals(action));
        if(PREVIEW.equals(action))beginPlayback(false,0);
        if(!store.enabled() && !PREVIEW.equals(action)){stopSelf();return START_NOT_STICKY;}
        return store.enabled()?START_STICKY:START_NOT_STICKY;
    }
    private void foreground(){
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notification=new Notification.Builder(this).setSmallIcon(android.R.drawable.ic_lock_silent_mode_off)
                .setContentTitle("i30 PD 부팅음").setContentText("ACC 상태 대기 · 무작위 음원 2개").setContentIntent(open).setOngoing(true).build();
        startForeground(71,notification);
    }
    private void beginMonitoring(boolean boot,boolean baseline){
        String id=ChimeStore.bootId();
        if(id==null){status("부팅 식별자를 읽지 못해 자동 재생을 중지했습니다.");return;}
        long now=SystemClock.elapsedRealtime();
        // Entry order (sticky service vs BOOT receiver) must not change a new boot.
        // A late manual launch must not produce a surprise startup voice.
        policy=new ChimePolicy(id,store.load(),now,now<600000,baseline);
        diagnostic("monitor_begin","bootAction="+boot+" baseline="+baseline+" bootSnapshotAllowed="+(now<600000)
                +" "+policyState(),true);
        if(!store.save(policy.snapshot())){policy=null;status("ACC 상태를 저장하지 못했습니다.");return;}
        source=new OemAccSource(this,handler,this);monitoring=true;handler.post(clockTick);source.start();
    }
    @Override public void onAccEvent(OemAccSource.Event event){
        if(policy==null)return;
        // Source timestamps are diagnostic; all policy calls use processing time.
        long now=SystemClock.elapsedRealtime();
        // Sticky uptime excludes sleep and cannot establish absolute freshness.
        ChimePolicy.Decision decision=policy.update(event.rawState,event.initialSnapshot,now);
        if(event.rawState!=ChimePolicy.ON && autoPlayback)cancelPlayback("ACC 해제로 재생을 취소했습니다.");
        diagnostic("policy_decision","raw="+event.rawState+" initial="+event.initialSnapshot
                +" reason="+decision.reason+" decisionToken="+decision.token+" cancel="+decision.cancel+" "+policyState(),true);
        if(!store.save(policy.snapshot())){cancelPlayback("상태 저장 실패로 재생을 생략했습니다.");return;}
        if(decision.token!=0)beginPlayback(true,decision.token);
        else if(decision.cancel && autoPlayback)cancelPlayback("ACC 상태 변경으로 재생을 취소했습니다.");
        Log.i(TAG,"ACC raw="+event.rawState+" snapshot="+event.initialSnapshot+" decision="+decision.reason);
    }
    @Override public void onSourceHealth(boolean healthy,String detail){
        if(policy!=null)policy.health(healthy,SystemClock.elapsedRealtime());
        if(!healthy && autoPlayback)cancelPlayback("ACC 연결 상태 확인 중입니다.");
        diagnostic("service_health","healthy="+healthy+" detail="+detail+" "+policyState(),true);
        if(player==null)status(healthy?"ACC 연결됨 · 다음 전원 주기 대기":("ACC 연결 확인 중: "+detail));
    }
    private void beginPlayback(boolean automatic,long token){
        cancelPlayback(null);
        autoPlayback=automatic;activeRequest=true;playbackToken=token;manualDeadline=SystemClock.elapsedRealtime()+ChimePolicy.START_DEADLINE_MS;
        handler.removeCallbacks(clockTick);handler.postDelayed(clockTick,500);
        int index=random.nextInt(CLIPS.length);store.selected(index);
        final long request=++generation;
        final String clip=CLIPS[index];
        diagnostic("playback_request","automatic="+automatic+" token="+token+" generation="+request+" clip="+clip
                +" path=oem_standard_media",true);
        status((automatic?"부팅음":"미리 듣기")+" 준비: 음원 "+(index+1));
        handler.postDelayed(new Runnable(){public void run(){if(generation==request && !started)cancelPlayback("준비 시간이 지나 재생을 생략했습니다.");}},ChimePolicy.START_DEADLINE_MS);
        acquireFocus(request,clip);
    }
    private boolean permitted(long request){
        if(generation!=request || started)return false;
        long now=SystemClock.elapsedRealtime();
        if(autoPlayback)return store.enabled() && policy!=null && policy.mayStart(playbackToken,now)
                && source!=null && source.isCurrentOn();
        return now<manualDeadline;
    }
    private boolean audioAllowed(){
        try{
            if(audio==null)return audioGuard(false,"audio=null");
            int mode=audio.getMode();
            if(mode!=AudioManager.MODE_NORMAL)return audioGuard(false,"mode="+mode);
            int volume=audio.getStreamVolume(AudioManager.STREAM_MUSIC);
            return audioGuard(volume>0,"mode="+mode+" musicVolume="+volume);
        }
        catch(RuntimeException e){return audioGuard(false,"error="+e.getClass().getSimpleName());}
    }
    private boolean audioGuard(boolean allowed,String detail){
        String state="allowed="+allowed+" "+detail;
        if(!state.equals(lastAudioGuard)){
            lastAudioGuard=state;
            // A final permission check must never wait for synchronous diagnostics.
            diagnostic("audio_guard",state,false);
        }
        return allowed;
    }
    private void scheduleRetry(final long request,final String clip){
        if(pendingRetry!=null)return;
        pendingRetry=new Runnable(){public void run(){
            if(pendingRetry!=this)return;
            pendingRetry=null;
            if(generation==request)acquireFocus(request,clip);
        }};
        handler.postDelayed(pendingRetry,500);
    }
    private void waitForAudio(long request,String clip,String reason){
        if(!autoPlayback || started || !permitted(request)){
            cancelPlayback("ACC 또는 준비 상태가 바뀌어 생략했습니다.");return;
        }
        // Preserve the one durable reservation, but hold no player/focus while waiting.
        releasePlaybackResources();
        if(!waitingForAudio){
            waitingForAudio=true;
            diagnostic("audio_wait","reason="+reason+" request="+request+" token="+playbackToken,true);
            status("AVN 오디오 준비 대기 · 최대 30초");
        }
        scheduleRetry(request,clip);
    }
    private void acquireFocus(final long request,final String clip){
        if(!permitted(request)){cancelPlayback("ACC 또는 준비 상태가 바뀌어 생략했습니다.");return;}
        if(!audioAllowed()){
            if(autoPlayback)waitForAudio(request,clip,"audio_guard");
            else cancelPlayback("음소거·통화 상태여서 재생을 생략했습니다.");
            return;
        }
        // Use the OEM's normal media path; the user accepts its Media UI transition.
        focus=new AudioManager.OnAudioFocusChangeListener(){public void onAudioFocusChange(final int value){
            final AudioManager.OnAudioFocusChangeListener owner=this;
            handler.post(new Runnable(){public void run(){
                boolean current=generation==request && focus==owner;
                if(current && value<0){
                    if(autoPlayback && !started)waitForAudio(request,clip,"focus_loss_"+value);
                    else cancelPlayback("다른 오디오 우선권으로 재생을 중지했습니다.");
                }
                diagnostic("focus_change","value="+value+" request="+request+" current="+current,true);
            }});
        }};
        int result;
        try{result=audio.requestAudioFocus(focus,AudioManager.STREAM_MUSIC,AudioManager.AUDIOFOCUS_GAIN_TRANSIENT);}
        catch(RuntimeException e){
            if(autoPlayback)waitForAudio(request,clip,"focus_error_"+e.getClass().getSimpleName());
            else cancelPlayback("오디오 준비 상태를 확인하지 못해 재생을 생략했습니다.");
            return;
        }
        if(result!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){
            releasePlaybackResources();
            diagnostic("focus_result","result="+result+" request="+request,true);
            if(autoPlayback)waitForAudio(request,clip,"focus_denied");
            else scheduleRetry(request,clip);
            return;
        }
        diagnostic("focus_result","result="+result+" request="+request,true);
        if(!permitted(request)){cancelPlayback("재생 조건이 바뀌어 생략했습니다.");return;}
        if(!audioAllowed()){
            if(autoPlayback)waitForAudio(request,clip,"audio_guard_after_focus");
            else cancelPlayback("재생 조건이 바뀌어 생략했습니다.");
            return;
        }
        if(waitingForAudio){waitingForAudio=false;diagnostic("audio_ready","request="+request,true);}
        try {
            final MediaPlayer current=new MediaPlayer();player=current;
            current.setAudioStreamType(AudioManager.STREAM_MUSIC);
            current.setLooping(false);
            current.setOnPreparedListener(new MediaPlayer.OnPreparedListener(){public void onPrepared(MediaPlayer value){
                diagnostic("player_prepared","request="+request+" current="+generation+" ownsPlayer="+(player==current),true);
                if(player!=current)return;
                if(!permitted(request)){cancelPlayback("준비 중 상태가 바뀌어 생략했습니다.");return;}
                if(!audioAllowed()){
                    if(autoPlayback)waitForAudio(request,clip,"audio_guard_when_prepared");
                    else cancelPlayback("준비 중 상태가 바뀌어 생략했습니다.");
                    return;
                }
                if(!permitted(request)){cancelPlayback("준비 중 상태가 바뀌어 생략했습니다.");return;}
                // Durable reservation was saved before loading. Never retry after this call.
                started=true;
                if(autoPlayback)policy.started(playbackToken);
                try{current.start();diagnostic("player_start_returned","automatic="+autoPlayback+" request="+request+" token="+playbackToken,true);status("음원 재생 중");}
                catch(RuntimeException e){cancelPlayback("재생 실패: "+e.getClass().getSimpleName());}
            }});
            current.setOnCompletionListener(new MediaPlayer.OnCompletionListener(){public void onCompletion(MediaPlayer value){if(player==current)cancelPlayback("재생 완료 · 다음 ACC 주기 대기");}});
            current.setOnErrorListener(new MediaPlayer.OnErrorListener(){public boolean onError(MediaPlayer value,int what,int extra){if(player==current)cancelPlayback("미디어 준비/재생 오류 "+what+"/"+extra);return true;}});
            AssetFileDescriptor asset=getAssets().openFd(clip);
            try{current.setDataSource(asset.getFileDescriptor(),asset.getStartOffset(),asset.getLength());}
            finally{asset.close();}
            current.prepareAsync();
            diagnostic("player_preparing","request="+request+" clip="+clip,false);
        }catch(Exception e){cancelPlayback("음원 준비 실패: "+e.getClass().getSimpleName());}
    }
    private void cancelPlayback(String message){
        String diagnosticCancel=(message!=null || player!=null || activeRequest)?"message="+message
                +" automatic="+autoPlayback+" started="+started+" request="+generation+" token="+playbackToken:null;
        generation++;started=false;activeRequest=false;
        if(pendingRetry!=null){handler.removeCallbacks(pendingRetry);pendingRetry=null;}
        releasePlaybackResources();waitingForAudio=false;
        if(autoPlayback && policy!=null)policy.cancel();
        autoPlayback=false;playbackToken=0;
        if(diagnosticCancel!=null)diagnostic("playback_cancel",diagnosticCancel,true);
        if(message!=null)status(message);
        if(message!=null && store!=null && !store.enabled() && !monitoring)stopSelf();
    }
    private void releasePlaybackResources(){
        MediaPlayer old=player;player=null;
        if(old!=null){try{old.reset();}catch(RuntimeException ignored){}try{old.release();}catch(RuntimeException ignored){}}
        AudioManager.OnAudioFocusChangeListener previous=focus;focus=null;
        if(previous!=null && audio!=null){try{audio.abandonAudioFocus(previous);}catch(RuntimeException ignored){}}
    }
    private void status(String value){store.status(value);Log.i(TAG,value);diagnostic("status",value,true);}
    private void diagnostic(String event,String detail,boolean critical){ChimeDiagnostics.record(this,event,detail,critical);}
    private String policyState(){return policy==null?"policy=null":savedState(policy.snapshot())+" playbackToken="+playbackToken;}
    private static String savedState(ChimePolicy.State state){return state==null?"null":"armed="+state.armed+" offSince="+state.offSince
            +" lastE="+state.lastElapsed+" seq="+state.sequence;}
    @Override public void onDestroy(){diagnostic("service_destroy","enabled="+store.enabled()+" "+policyState(),true);
        monitoring=false;handler.removeCallbacksAndMessages(null);if(source!=null)source.stop();cancelPlayback(null);stopForeground(true);super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
