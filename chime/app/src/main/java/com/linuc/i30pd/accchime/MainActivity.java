package com.linuc.i30pd.accchime;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.content.Intent;
import android.graphics.Color;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private final Handler handler=new Handler();
    private ChimeStore store;
    private TextView status;
    private Button enabled;
    private final Runnable refresh=new Runnable(){public void run(){update();handler.postDelayed(this,1000);}};
    @Override public void onCreate(Bundle state) {
        super.onCreate(state); store=new ChimeStore(this);
        ChimeDiagnostics.record(this,"activity_create","enabled="+store.enabled(),false);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(24,16,24,16);
        root.setBackgroundColor(Color.rgb(16,22,28));
        TextView title=new TextView(this);title.setText("i30 PD 부팅음");title.setTextSize(25);title.setTextColor(Color.WHITE);root.addView(title);
        TextView info=new TextView(this);info.setText("ACC가 켜지면 두 음원 중 하나를 무작위로 한 번 재생합니다.\n사용을 켠 현재 전원에서는 자동 재생하지 않고, 다음 ACC 주기·부팅부터 적용합니다.\n통화·음소거·재생 우선권 제한 시에는 생략할 수 있습니다.");
        info.setTextSize(16);info.setTextColor(Color.LTGRAY);root.addView(info);
        enabled=new Button(this);root.addView(enabled);enabled.setOnClickListener(new View.OnClickListener(){public void onClick(View v){
            boolean next=!store.enabled(); if(!store.enable(next)){store.status("설정을 저장하지 못했습니다.");update();return;}
            ChimeDiagnostics.record(MainActivity.this,"enable","enabled="+next,true);
            if(next)startService(new Intent(MainActivity.this,ChimeService.class).setAction(ChimeService.ENABLE));
            else stopService(new Intent(MainActivity.this,ChimeService.class));update();
        }});
        Button preview=new Button(this);preview.setText("무작위 미리 듣기");root.addView(preview);
        preview.setOnClickListener(new View.OnClickListener(){public void onClick(View v){startService(new Intent(MainActivity.this,ChimeService.class).setAction(ChimeService.PREVIEW));}});
        LinearLayout actions=new LinearLayout(this);actions.setOrientation(LinearLayout.HORIZONTAL);root.addView(actions);
        Button stop=new Button(this);stop.setText("재생 중지");actions.addView(stop,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1));
        stop.setOnClickListener(new View.OnClickListener(){public void onClick(View v){startService(new Intent(MainActivity.this,ChimeService.class).setAction(ChimeService.CANCEL));}});
        Button history=new Button(this);history.setText("상태 기록");actions.addView(history,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1));
        history.setOnClickListener(new View.OnClickListener(){public void onClick(View v){showHistory();}});
        status=new TextView(this);status.setTextColor(Color.WHITE);status.setTextSize(15);root.addView(status);setContentView(root);
        if(store.enabled())startService(new Intent(this,ChimeService.class).setAction(ChimeService.RESUME));
    }
    private void showHistory(){
        TextView text=new TextView(this);text.setTextSize(14);text.setTextColor(Color.WHITE);text.setPadding(16,12,16,12);
        text.setTextIsSelectable(true);text.setText("최근 상태 기록 · 최대 128개\n시간: w=시각, e=절전 포함 경과, u=동작 경과 (ms)\n\n"+ChimeDiagnostics.read(this));
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.addView(text);
        new AlertDialog.Builder(this).setTitle("상태 기록").setView(scroll).setPositiveButton("닫기",null).show();
    }
    private void update(){if(store==null)return;enabled.setText(store.enabled()?"부팅음 사용: 켜짐":"부팅음 사용: 꺼짐");status.setText(store.prefs.getString("status","미리 듣기로 두 음원을 확인하세요.")+"\n최근 선택: "+store.prefs.getInt("last_clip",0));}
    @Override public void onResume(){super.onResume();handler.post(refresh);}
    @Override public void onPause(){handler.removeCallbacks(refresh);super.onPause();}
}
