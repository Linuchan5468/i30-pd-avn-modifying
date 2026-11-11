package com.linuc.i30pd.accchime;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Handler;
import android.os.IBinder;
import android.os.IInterface;
import android.os.Looper;
import android.os.SystemClock;

import java.lang.reflect.Field;

/** Passive Gen5 BCM subscription. All application callbacks run on the supplied handler. */
public final class OemAccSource {
    public static final int UNKNOWN = -1;
    public static final int OFF = 0;
    public static final int PRESYSTEMOFF = 1;
    public static final int LOGICON = 2;
    public static final int DELAYMODE = 3;
    public static final int ADM = 4;
    public static final int ON = 5;
    private static final long PROBE_MS = 3000L;
    private static final long RETRY_MS = 5000L;
    private static final long INITIAL_STATE_TIMEOUT_MS = 15000L;
    public static final int UNKNOWN_POWER_STATUS = -1;
    private static final String POWER_ACTION = "com.hkmc.intent.action.ACTION_ACC_STATUS_CHANGED";
    private static final String POWER_STATE = "android.ibox.mode.EXTRA_ACC_STATUS";
    private static final String POWER_PREVIOUS = "android.ibox.mode.EXTRA_PREVIOUS_ACC_STATUS";
    private static final String POWER_TIMESTAMP = "android.ibox.mode.TIMESTAMP";

    public interface Listener {
        void onAccEvent(Event event);
        void onSourceHealth(boolean healthy, String detail);
    }

    public static final class Event {
        public final int rawState;
        public final long receivedElapsedMs;
        public final long receivedWallTimeMs;
        /** Optional matching power-broadcast dispatch uptime; not a physical-edge timestamp. */
        public final long sourceTimestampMs;
        public final String sourceTimestampClock;
        /** Separate MicomPower enum; these values must never be treated as BCM states. */
        public final int powerStatus;
        public final int previousPowerStatus;
        /** Neither BCM nor the sticky power broadcast supplies a persistent session identifier. */
        public final String sessionId = null;
        public final String source = "bcm_listener";
        /** Registration replay, including the first recognized asynchronous cached callback. */
        public final boolean initialSnapshot;

        private Event(int state, boolean snapshot, long elapsed, long wall, long[] powerSnapshot) {
            rawState = state;
            initialSnapshot = snapshot;
            receivedElapsedMs = elapsed;
            receivedWallTimeMs = wall;
            sourceTimestampMs = powerSnapshot[0];
            sourceTimestampClock = sourceTimestampMs < 0L ? null : "uptimeMillis";
            powerStatus = (int) powerSnapshot[1];
            previousPowerStatus = (int) powerSnapshot[2];
        }
    }

    private final Context context;
    private final Handler handler;
    private final Listener listener;
    private Backend backend;
    private boolean active;
    private boolean receivedKnownState;
    private boolean healthReported;
    private boolean lastHealth;
    private String lastHealthDetail;
    private int generation;
    private long connectedElapsedMs;
    private int diagnosticCachedState = Integer.MIN_VALUE;

    public OemAccSource(Context context, Handler handler, Listener listener) {
        if (context == null || handler == null || listener == null) {
            throw new IllegalArgumentException("Context, handler and listener are required");
        }
        Context application = context.getApplicationContext();
        this.context = application == null ? context : application;
        this.handler = handler;
        this.listener = listener;
    }

    /** Idempotent. Call start and stop from the supplied handler's looper. */
    public void start() {
        requireHandlerThread();
        if (active) return;
        active = true;
        ChimeDiagnostics.record(context, "source_start", "generation=" + generation, true);
        generation++;
        healthReported = false;
        reportHealth(false, "Connecting to OEM BCM");
        connect();
    }

    public void stop() {
        requireHandlerThread();
        if (!active) return;
        active = false;
        ChimeDiagnostics.record(context, "source_stop", "generation=" + generation, true);
        generation++;
        handler.removeCallbacks(probe);
        handler.removeCallbacks(retry);
        closeBackend();
        reportHealth(false, "OEM BCM source stopped");
    }

    public static boolean isKnownState(int state) {
        return state >= OFF && state <= ON;
    }

    /** Last-moment passive guard for an automatic player start; never emits an ACC edge. */
    public boolean isCurrentOn() {
        requireHandlerThread();
        if (!active || backend == null || !lastHealth) {
            ChimeDiagnostics.record(context, "source_start_guard", "allowed=false active=" + active
                    + " backend=" + (backend != null) + " healthy=" + lastHealth, true);
            return false;
        }
        try {
            int cached = backend.readState();
            if (cached != ON) ChimeDiagnostics.record(context, "source_start_guard", "allowed=false cached=" + cached, true);
            return cached == ON;
        }
        catch (Exception error) {
            ChimeDiagnostics.record(context, "source_start_guard", "allowed=false error=" + error.getClass().getSimpleName(), true);
            return false;
        }
        catch (LinkageError error) {
            ChimeDiagnostics.record(context, "source_start_guard", "allowed=false error=" + error.getClass().getSimpleName(), true);
            return false;
        }
    }

    private void requireHandlerThread() {
        if (Looper.myLooper() != handler.getLooper()) {
            throw new IllegalStateException("Call on the source handler looper");
        }
    }

    private final Runnable retry = new Runnable() {
        @Override public void run() { if (active && backend == null) connect(); }
    };

    private final Runnable probe = new Runnable() {
        @Override public void run() {
            if (!active || backend == null) return;
            try {
                int cached = backend.readState();
                if (cached != diagnosticCachedState) {
                    diagnosticCachedState = cached;
                    ChimeDiagnostics.record(context, "source_probe", "cached=" + cached
                            + " receivedKnown=" + receivedKnownState, true);
                }
                if (!receivedKnownState && SystemClock.elapsedRealtime() - connectedElapsedMs
                        >= INITIAL_STATE_TIMEOUT_MS) {
                    unavailable("OEM BCM initial state timed out", null);
                    return;
                }
                // This getter reads a cache. It verifies availability, never invents an edge.
                reportHealth(isKnownState(cached), isKnownState(cached)
                        ? "OEM BCM available (cached state)" : "OEM BCM state unknown");
                handler.postDelayed(this, PROBE_MS);
            } catch (Exception error) {
                unavailable("OEM BCM getter failed", error);
            } catch (LinkageError error) {
                unavailable("OEM BCM getter unavailable", error);
            }
        }
    };

    private void connect() {
        if (!active || backend != null) return;
        final int token = ++generation;
        receivedKnownState = false;
        diagnosticCachedState = Integer.MIN_VALUE;
        connectedElapsedMs = SystemClock.elapsedRealtime();
        try {
            // Keep OEM references inside this lazily loaded class so missing automotive classes
            // can be reported as unavailability on an ordinary Android device.
            backend = new BcmBackend(context, new Reporter() {
                @Override public void state(final int state, final boolean registrationReplay) {
                    final long elapsed = SystemClock.elapsedRealtime();
                    final long wall = System.currentTimeMillis();
                    handler.post(new Runnable() {
                        @Override public void run() {
                            if (!active || token != generation || backend == null) {
                                ChimeDiagnostics.record(context, "source_callback_drop", "raw=" + state
                                        + " registrationReplay=" + registrationReplay + " staleGeneration=true", true);
                                return;
                            }
                            boolean snapshot = registrationReplay || !receivedKnownState;
                            try {
                                // The framework swallows register RemoteException. Check its
                                // Binder and cached getter before accepting a replay as healthy.
                                int cached = backend.readState();
                                ChimeDiagnostics.record(context, "source_callback", "raw=" + state
                                        + " cached=" + cached + " registrationReplay=" + registrationReplay
                                        + " initial=" + snapshot + " receivedE=" + elapsed, false);
                                if (isKnownState(state) && cached != state) {
                                    // A queued ON may already be stale by the time we receive it.
                                    // Revoke permission and await a callback; never promote the
                                    // getter's newer cache value into an invented physical edge.
                                    reportHealth(false, "OEM BCM callback/cache mismatch");
                                    return;
                                }
                                reportHealth(isKnownState(state) && isKnownState(cached),
                                        isKnownState(state) && isKnownState(cached)
                                                ? "OEM BCM available (cached state)"
                                                : "OEM BCM state unknown");
                            } catch (Exception error) {
                                unavailable("OEM BCM getter failed", error);
                                return;
                            } catch (LinkageError error) {
                                unavailable("OEM BCM getter unavailable", error);
                                return;
                            }
                            if (active && token == generation) {
                                if (isKnownState(state)) receivedKnownState = true;
                                Event event = new Event(state, snapshot, elapsed, wall, matchingPowerSnapshot(state));
                                ChimeDiagnostics.record(context, "source_deliver", "raw=" + state
                                        + " initial=" + snapshot + " power=" + event.powerStatus
                                        + " previousPower=" + event.previousPowerStatus + " sourceU=" + event.sourceTimestampMs, false);
                                listener.onAccEvent(event);
                            }
                        }
                    });
                }

                @Override public void died() {
                    handler.post(new Runnable() {
                        @Override public void run() {
                            if (active && token == generation) {
                                unavailable("OEM BCM Binder disconnected", null);
                            }
                        }
                    });
                }
            });
            backend.open();
            handler.post(probe);
        } catch (Exception error) {
            unavailable("OEM BCM subscription unavailable", error);
        } catch (LinkageError error) {
            unavailable("OEM automotive library unavailable", error);
        }
    }

    private void unavailable(String detail, Throwable error) {
        ChimeDiagnostics.record(context, "source_unavailable", detail + (error == null ? ""
                : " error=" + error.getClass().getSimpleName()), true);
        generation++;
        handler.removeCallbacks(probe);
        closeBackend();
        if (error != null) detail += " (" + error.getClass().getSimpleName() + ")";
        reportHealth(false, detail);
        handler.removeCallbacks(retry);
        if (active) handler.postDelayed(retry, RETRY_MS);
    }

    private void closeBackend() {
        Backend old = backend;
        backend = null;
        if (old != null) old.close();
    }

    private void reportHealth(boolean healthy, String detail) {
        if (!healthReported || healthy != lastHealth || !detail.equals(lastHealthDetail)) {
            healthReported = true;
            lastHealth = healthy;
            lastHealthDetail = detail;
            listener.onSourceHealth(healthy, detail);
            ChimeDiagnostics.record(context, "source_health", "healthy=" + healthy + " detail=" + detail, true);
        }
    }

    private long[] matchingPowerSnapshot(int bcmState) {
        long[] absent = { -1L, UNKNOWN_POWER_STATUS, UNKNOWN_POWER_STATUS };
        if (bcmState != ON) return absent;
        try {
            Intent sticky = context.registerReceiver(null, new IntentFilter(POWER_ACTION));
            if (sticky == null || !POWER_ACTION.equals(sticky.getAction())
                    || !sticky.hasExtra(POWER_STATE) || !sticky.hasExtra(POWER_TIMESTAMP)) {
                return absent;
            }
            // This namespace's ACCON is 0, whereas BCM's ON is 5. Match explicitly.
            int powerState = sticky.getIntExtra(POWER_STATE, UNKNOWN_POWER_STATUS);
            long timestamp = sticky.getLongExtra(POWER_TIMESTAMP, -1L);
            if (powerState != 0 || timestamp < 0L || timestamp > SystemClock.uptimeMillis()) {
                return absent;
            }
            int previous = sticky.getIntExtra(POWER_PREVIOUS, UNKNOWN_POWER_STATUS);
            if (previous != 0 && previous != 1 && previous != 2 && previous != 5
                    && previous != 6 && previous != 7 && previous != 255) {
                previous = UNKNOWN_POWER_STATUS;
            }
            return new long[] { timestamp, powerState, previous };
        } catch (RuntimeException ignored) {
            // Optional untrusted metadata never changes the authoritative BCM state or health.
            return absent;
        }
    }

    private interface Reporter {
        void state(int state, boolean registrationReplay);
        void died();
    }

    private interface Backend {
        void open() throws Exception;
        int readState() throws Exception;
        void close();
    }

    /** Direct listener ABI from stored V141 automotive.jar; stubs are compile-only. */
    private static final class BcmBackend implements Backend, IBinder.DeathRecipient {
        private final com.hkmc.bcm.BCMManager manager;
        private final com.hkmc.bcm.BCMPowerListener powerListener;
        private final IBinder binder;
        private final Reporter reporter;
        private volatile boolean registering;
        private boolean addAttempted;
        private boolean linked;

        BcmBackend(Context context, final Reporter reporter) throws Exception {
            this.reporter = reporter;
            manager = new com.hkmc.bcm.BCMManager(context);
            // Verified private field. This reads transport health only; no OEM command is sent.
            Field serviceField = manager.getClass().getDeclaredField("mService");
            serviceField.setAccessible(true);
            Object service = serviceField.get(manager);
            if (!(service instanceof IInterface)) {
                throw new IllegalStateException("OEM BCM Binder absent");
            }
            binder = ((IInterface) service).asBinder();
            if (binder == null || !binder.isBinderAlive()) {
                throw new IllegalStateException("OEM BCM Binder unavailable");
            }
            powerListener = new com.hkmc.bcm.BCMPowerListener() {
                @Override public void onACCChanged(int state) {
                    reporter.state(state, registering);
                }
            };
        }

        @Override public void open() throws Exception {
            binder.linkToDeath(this, 0);
            linked = true;
            registering = true;
            addAttempted = true;
            try { manager.addPowerListener(powerListener); }
            finally { registering = false; }
        }

        @Override public int readState() {
            if (!binder.isBinderAlive()) throw new IllegalStateException("OEM BCM Binder died");
            return manager.getEngineStatus();
        }

        @Override public void binderDied() { reporter.died(); }

        @Override public void close() {
            // Remove even if registration threw after partially installing the listener.
            if (addAttempted) {
                addAttempted = false;
                try { manager.removePowerListener(powerListener); }
                catch (RuntimeException ignored) { }
                catch (LinkageError ignored) { }
            }
            if (linked) {
                linked = false;
                try { binder.unlinkToDeath(this, 0); }
                catch (RuntimeException ignored) { }
            }
        }
    }
}
