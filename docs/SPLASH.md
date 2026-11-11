# Splash 교체와 복원

## 저장 구조

확인된 Splash는 파일시스템 파일이 아니라 `mmcblk0p7`의 8 MiB 원시 데이터입니다. 5개의 무압축 24비트 BMP가 `0x150000` 간격으로 들어 있습니다. 0번 기본 부팅과 3번 Bluelink 이미지는 800×480이고, 4번 복구 이미지는 800×100입니다. 도구는 **0·3번의 픽셀 영역만** 바꿉니다. 헤더·BMP 끝의 추가 바이트·1/2/4번 기능 이미지·파티션 뒤쪽 바이트는 그대로 둡니다.

| 슬롯 | 역할 | 처리 |
| --- | --- | --- |
| 0 | 기본 부팅 이미지 | 본인 이미지의 픽셀로 교체 |
| 1 | 순정 기능 이미지 | 보존 |
| 2 | Quickboot 이미지 | 보존 |
| 3 | Bluelink 브랜드 이미지 | 본인 이미지의 픽셀로 교체 |
| 4 | 복구 이미지 | 보존 |

빠른 재기동에서 2번 이미지가 계속 보일 수 있습니다. 이 도구는 모든 시작 경로의 화면을 바꾸는 도구가 아닙니다. 업데이트 패키지 전체를 다시 서명하거나 MICOM·부트로더를 쓰지 않습니다.

## 준비와 백업

전원이 안정적인 정차 상태와 정상 데이터 연결을 유지하세요. 파티션 쓰기 중 전원·연결을 잃으면 복원 명령도 실행할 수 없으므로, PC와 차량의 전원을 중간에 끄지 마세요. `p8`, `p9`는 대상이 아닙니다.

```powershell
python tools/splash.py check --serial SERIAL --profile pd-kr-v141 --out work/check-01
python tools/splash.py backup --serial SERIAL --profile pd-kr-v141 --out work/backup-01
python tools/splash.py inspect work/backup-01/original.img
```

`check`는 제품·모델·빌드·펌웨어, 블록 179:7, 크기 16,384×512바이트, 비마운트 상태, 기존 root 도구의 해시·권한을 검사합니다. 다른 구조를 자동 탐색해 쓰지 않습니다. `backup`은 서로 다른 전체 읽기 두 개를 PC로 가져와 같은지 비교합니다. `original.img`, `second-read.img`, `backup.json`을 함께 별도 위치에도 복사해 두세요.

## 본인 이미지로 변환

이미지는 정확히 **800×480, RGB 또는 완전 불투명 RGBA PNG**로 준비하세요. 도구가 자르거나 늘이지 않습니다. Pillow는 색 채널을 BMP BGR·아래에서 위 순서로 인코딩하는 데만 씁니다.

```powershell
python tools/splash.py prepare --backup work/backup-01 --artwork "C:\MyImages\splash.png" --out work/plan-01
```

결과는 `original.img`, `candidate.img`, `plan.json`입니다. 후보는 본인 차량에서 읽은 전체 원본에서 만들어지며, 다른 차량의 순정 이미지를 대신 넣지 않습니다. 후보 크기는 8 MiB이고 변경 가능한 범위는 `[54,1152054)`, `[4128822,5280822)` 두 곳뿐입니다. 각 사용자 원본 SHA-256을 계획에 기록합니다. 연구 차량의 원본 해시가 모든 차량에서 같다고 가정하지 않습니다.

## 적용과 복원

아래 명령부터 파티션을 씁니다. `--write-p7`을 생략하면 장치에 연결하기 전에 중단합니다.

```powershell
python tools/splash.py apply --serial SERIAL --profile pd-kr-v141 --plan work/plan-01 --out work/apply-01 --write-p7
```

도구는 원본·후보의 형식과 해시를 다시 확인하고, 두 파일을 AVN에 보낸 뒤 **전송된 파일도 다시 내려받아** 바이트를 비교합니다. 쓰기 직전 전체 파티션이 계획의 원본과 같아야 합니다. 순정 `dd`와 `sync`로 고정 대상만 복사한 후, 전체 파티션을 독립적으로 두 번 읽어 후보와 비교합니다. 실제 재부팅은 하지 않습니다. `receipt.json`의 `apply_two_full_readbacks_verified`를 확인한 뒤 사용자가 직접 정상 재부팅할 수 있습니다.

확인된 후보 상태에서 원래 화면으로 복원하려면 같은 계획 폴더를 사용합니다.

```powershell
python tools/splash.py restore --serial SERIAL --profile pd-kr-v141 --plan work/plan-01 --out work/restore-01 --write-p7
```

복원도 전송 확인·쓰기 직전 후보 해시·`sync`·전체 읽기 두 번을 요구합니다. 알 수 없는 현재 바이트를 덮어쓰는 강제 옵션은 제공하지 않습니다. 적용/복원 기록 폴더에는 실행 직전 전체 백업과 양쪽 파일이 남습니다.

적용·복원은 AVN의 `/data/local/tmp/i30pd-splash-p7.lock` 디렉터리를 원자적으로 생성해 독점합니다. 전송·쓰기 직전 비교·쓰기·검증·실패 복원까지 잠금을 유지하고, 본인의 무작위 토큰이 일치할 때만 해제합니다. 다른 실행이 잠금을 갖고 있으면 쓰기 전에 중단합니다. 이 도구를 거치지 않는 수동 `dd`까지 차단하는 잠금은 아닙니다.

연결 단절이나 프로세스 강제 종료 후 잠금이 남으면 자동 제거하지 않습니다. 먼저 다른 PC/터미널의 적용·복원 작업이 모두 종료됐는지 확인하고, 남은 `receipt.json`과 현재 연결을 점검하세요. **진행 중인 쓰기·검증·복원이 없음을 확인한 경우에만** `adb -s SERIAL shell "cat /data/local/tmp/i30pd-splash-p7.lock/owner"`로 남은 토큰을 확인하고 아래 명령으로 잠금을 제거합니다. 불확실하면 제거하지 마세요.

```powershell
adb -s SERIAL shell "rm /data/local/tmp/i30pd-splash-p7.lock/owner && rmdir /data/local/tmp/i30pd-splash-p7.lock"
```

쓰기나 검증 실패 시 도구는 바로 직전의 검증된 이미지로 되돌리기를 시도하고 두 번 읽습니다. 이는 전원 손실에 대한 원자적 복구를 보장하지 않습니다. 실패 시 `receipt.json`의 `rollback_verified`를 확인하세요. `false`이면 재부팅하지 말고 파일과 연결 상태를 보존해 개별 점검해야 합니다. 일반 ADB 오류를 해결하려고 다른 파티션이나 임의 이미지에 쓰지 마세요.

## 실차 확인과 공개 도구의 관계

2026-11-10 이 구조와 순정 `dd` 경로로 교체했고 전체 읽기 두 번이 일치했습니다. 재부팅 후 차량 소유자가 새 이미지가 정상 표시됨을 확인했습니다. 공개 도구는 당시 단일 차량 스크립트를 일반화한 것이며, 공개판 자체의 실차 재시험은 아직 하지 않았습니다.
