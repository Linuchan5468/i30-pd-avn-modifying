# ADB 연결

이 안내는 국내 표준형 5세대 + 블루링크가 적용된 i30 PD의 `PD.KOR.SOP.V141.260114.STD_H` 코드 분석과 2026-10-27 실차 ADB 셸 연결 확인에 근거합니다. 다른 세대·지역·펌웨어의 명령을 보장하지 않습니다.

## 차량에서

1. 정차 상태에서 AVN을 정상 부팅하고 **Engineering mode → Diagnosis → Factory Test** 입력칸을 엽니다.
2. 아래 한 줄을 입력하고 **SEND**를 한 번 누릅니다. 바이트 사이에 공백 한 칸을 둡니다.

   ```text
   0A 01
   ```

3. 약 1초 기다린 뒤 입력칸을 비우고 다음 한 줄을 별도로 SEND합니다.

   ```text
   65 01
   ```

4. AVN의 USB 역할과 전원 배선이 맞는 데이터 연결로 PC에 연결합니다. 사용 차량은 USB A-A 케이블을 통해 연결하였습니다.
   일반적인 A-C의 경우 C 단자 쪽 전원 호스트를 강제하기 위한 저항(Resistor)이 있을 가능성이 높습니다.
   A-A 케이블 또는 C Female - A Male 젠더를 사용하는것을 권장합니다.

Engineering mode의 2026년 기준 비밀번호는 `0567`이였습니다.

## PC에서

[공식 Platform-Tools](https://developer.android.com/tools/releases/platform-tools)를 준비하고 실행합니다.

```powershell
adb devices -l
adb -s SERIAL shell id
adb -s SERIAL shell getprop ro.build.product2
adb -s SERIAL shell getprop ro.product.name
adb -s SERIAL shell getprop sys.usb.config
adb -s SERIAL shell getprop persist.sys.dipo.state
```

장치 상태가 `device`여야 합니다. `offline`은 정상 연결이 아니며 케이블·USB 역할·AVN의 ADB 상태를 확인한 뒤 다시 연결합니다. `unauthorized`이면 장치의 인증 화면을 확인하세요. 여러 장치가 연결될 수 있으므로 이후 명령에는 항상 본인 장치의 `-s SERIAL`을 붙입니다.

확인 당시 일반 ADB 셸은 `uid=2000(shell)`이었고 `sys.usb.config=adb`, `persist.sys.dipo.state=debug`, `sys.factory.enabled=true`였습니다. ADB 연결 자체가 root를 의미하지 않습니다.

## 작동 원리

첫 명령은 FactoryService의 공장 진단 상태를 켭니다. 이 상태가 아니면 두 번째 명령은 처리되지 않습니다. 두 번째 명령은 Modem OTG 분기를 거쳐 Engineering 앱의 `MiscManager.setSystem(5)`로 이어지며 USB debug 상태를 요청합니다. 숨겨진 ADB 메뉴의 표시 조건을 거치지 않습니다. 이는 임의의 CAN 프레임을 전송하는 절차와 다릅니다. 명령 번호를 추측해 바꾸지 마세요.

## Splash에서 사용하는 root 경로

확인된 순정 파일 `/system/bin/amossu`는 root 소유의 SUID/SGID 실행 파일(모드 `06755`)입니다. 실제 확인한 호출은 다음과 같습니다.

```powershell
adb -s SERIAL shell "/system/bin/amossu -c 'id'"
```

해당 차량에서만 `uid=0(root)` 반환을 확인했습니다. 공개 Splash 도구는 먼저 이 파일의 SHA-256을 확인하며 아래 값과 다르면 중단합니다.

```text
5933f058153591052254ce6db5193b4f511be2cc6e0aa00144ea9654af5c25c0
```

파일을 새로 설치하거나 `adbd`를 root로 바꾸지 않습니다. `/system` 재마운트나 공장 root 전환 명령도 사용하지 않습니다. 부팅음 앱은 이 경로를 호출하지 않습니다.

## 해제 명령의 확인 수준

코드 분석상 USB debug 해제는 `65 00`, 공장 진단 모드 해제는 `0A 02`를 각각 SEND합니다. `0A 00`은 이 V141의 공장 모드 종료 명령이 아닙니다. 해제하면 현재 ADB 연결이 끊어질 수 있으므로 백업·검증 중에는 실행하지 마세요.
