package com.linuc.i30pd.accchime;

/** Pure ACC policy. One durable reservation per observed/debounced cycle. */
public final class ChimePolicy {
    public static final long OFF_DEBOUNCE_MS = 5000;
    public static final long START_DEADLINE_MS = 30000;
    public static final int UNKNOWN = -1, OFF = 0, ON = 5;
    public static final class State {
        public final String bootId;
        public final boolean armed;
        public final long offSince, lastElapsed, sequence;
        public State(String bootId, boolean armed, long offSince, long lastElapsed, long sequence) {
            this.bootId=bootId; this.armed=armed; this.offSince=offSince;
            this.lastElapsed=lastElapsed; this.sequence=sequence;
        }
    }
    public static final class Decision {
        public final long token;
        public final boolean cancel;
        public final String reason;
        Decision(long token, boolean cancel, String reason) {
            this.token=token; this.cancel=cancel; this.reason=reason;
        }
    }
    private final String bootId;
    private final long initialDeadline;
    private boolean armed, initialBootAllowed, healthy;
    private long offSince=-1, lastElapsed, sequence, token, deadline;
    private int state=UNKNOWN;

    public ChimePolicy(String bootId, State saved, long now, boolean allowBootSnapshot, boolean baselineCurrent) {
        if (bootId==null || bootId.length()==0 || now<0) throw new IllegalArgumentException("boot/time");
        this.bootId=bootId; lastElapsed=now; initialDeadline=now+START_DEADLINE_MS;
        boolean sameBoot=saved!=null && bootId.equals(saved.bootId);
        if (sameBoot && now>=saved.lastElapsed) {
            armed=saved.armed; offSince=-1; sequence=saved.sequence;
        } else if(sameBoot) {
            armed=false; initialBootAllowed=false; sequence=saved.sequence;
        } else {
            armed=saved!=null;
            initialBootAllowed=saved!=null && allowBootSnapshot;
        }
        if (baselineCurrent) { armed=false; offSince=-1; initialBootAllowed=false; }
    }
    public State snapshot() { return new State(bootId,armed,offSince,lastElapsed,sequence); }
    /** BCMService.onAccOff stores its reason (0..4); onAccOn stores 5. */
    private static boolean isOffReason(int raw) { return raw>=OFF && raw<ON; }
    private void clock(long now) {
        if (now<lastElapsed) { offSince=-1; armed=false; initialBootAllowed=false; cancel(); }
        lastElapsed=now;
    }
    public boolean tick(long now) {
        clock(now);
        if (isOffReason(state) && offSince>=0 && now-offSince>=OFF_DEBOUNCE_MS && !armed) {
            armed=true; return true;
        }
        return false;
    }
    public void health(boolean available, long now) {
        clock(now); healthy=available;
        if (!available) { offSince=-1; cancel(); }
    }
    public Decision update(int raw, boolean initialSnapshot, long now) {
        clock(now);
        // Account for a long OFF even when its timer has not run yet.
        if (isOffReason(state) && offSince>=0 && now-offSince>=OFF_DEBOUNCE_MS) armed=true;
        int previousState=state;
        state=raw;
        if (raw!=ON) {
            cancel();
            // Startup OFF reasons are not a consumed new-boot ON snapshot.
            if (isOffReason(raw)) { if (offSince<0) offSince=now; }
            else { offSince=-1; armed=false; } // unknown/invalid states cannot prove stable OFF
            return new Decision(0,true,"not_on");
        }
        offSince=-1;
        if (!healthy) return new Decision(0,true,"source_unavailable");
        if(now>=initialDeadline) initialBootAllowed=false;
        boolean bootAllowed=initialBootAllowed;
        if(previousState==ON && !armed && !bootAllowed) return new Decision(0,false,"duplicate_on");
        if (initialSnapshot && !initialBootAllowed) {
            // A cached ON after process death cannot identify an unseen cycle.
            armed=false; cancel(); return new Decision(0,true,"cached_on_baseline");
        }
        initialBootAllowed=false;
        if (!armed && !bootAllowed) return new Decision(0,false,"already_reserved_or_baseline");
        armed=false;
        sequence++;
        if (sequence<=0) sequence=1;
        token=sequence; deadline=now+START_DEADLINE_MS;
        return new Decision(token,false,"reserved_once");
    }
    public boolean mayStart(long requestedToken,long now) {
        clock(now);
        if (token==0 || token!=requestedToken || state!=ON || !healthy || now>=deadline) return false;
        return true;
    }
    public void started(long requestedToken) { if (token==requestedToken) cancel(); }
    public void cancel() { token=0; deadline=0; }
}
