# i30 PD AVN Modifying

i30 PD의 표준형 5세대 AVN에서 **시작 이미지(Splash) 교체**와 **ACC ON 부팅음**을 구현한 개인 연구 프로젝트입니다. 실제 사용한 원리와 소스, 적용 도구를 정리했습니다. 이미지와 음원은 사용자가 직접 준비합니다.

확인된 환경은 국내 i30 PD 블루링크 옵션이 적용된 표준형 5세대 AVN이며, 아래 펌웨어 이외의 호환성은 확인하지 않았습니다. 차종 이름이 같아도 다른 펌웨어에 그대로 적용하지 마세요. 스크립트는 지원 프로필과 파티션 구조가 다르면 중단합니다.

| 항목 | 확인된 값 |
| --- | --- |
| 제품 / 모델 | `pd_kr` / `daudioplus` |
| 펌웨어 | `PD.KOR.SOP.V141.260114.STD_H` |
| Android / 빌드 | 4.2.2(API 17) / `eng.vmadmin.20260114.084130` |
| Splash | `/dev/block/mmcblk0p7`, 179:7, 8 MiB |
| 실제 확인 | 새 Splash 부팅 표시, 부팅음 0.1.4-test의 ACC 전환 후 1회 자동 재생 |

## 먼저 읽기

GitHub의 **Code → Download ZIP**으로 내려받아 압축을 푼 뒤, 아래 안내를 순서대로 진행하세요. Git을 사용하는 경우 다음 명령으로 준비할 수 있습니다.

```powershell
git clone https://github.com/Linuchan5468/i30-pd-avn-modifying.git
cd i30-pd-avn-modifying
```

- Engineering mode의 Diagnosis 입력 경로로 ADB를 연결합니다. [ADB 연결](docs/ADB.md)
- 본인 차량을 두 번 읽어 백업하고, 800×480 이미지를 변환한 뒤 적용합니다. [Splash 교체와 복원](docs/SPLASH.md)
- 본인 WAV 2개를 준비하고, Android API 17 APK를 빌드합니다. [부팅음 빌드·설치](docs/BOOT_CHIME.md)
- 실차 확인 결과와 공개 스크립트의 검사 결과를 확인할 수 있습니다. [검증 범위](docs/VALIDATION.md)

## Splash 빠른 시작

PC에 Python 3.10 이상과 [Android Platform-Tools](https://developer.android.com/tools/releases/platform-tools)를 설치하고, 이 폴더에서 PowerShell을 여세요. Pillow는 이미지 변환에만 사용합니다. `SERIAL`은 `adb devices -l`에 표시된 본인 장치 값으로 바꾸세요.

```powershell
python -m pip install -r requirements.txt
adb devices -l
python tools/splash.py check --serial SERIAL --profile pd-kr-v141 --out work/check-01
python tools/splash.py backup --serial SERIAL --profile pd-kr-v141 --out work/backup-01
python tools/splash.py prepare --backup work/backup-01 --artwork "C:\MyImages\splash.png" --out work/plan-01
```

여기까지는 AVN 파티션을 쓰지 않습니다. `work/plan-01/plan.json`과 원본 백업을 확인한 뒤 [적용·복원 명령](docs/SPLASH.md#적용과-복원)을 사용하세요. 각 `--out`에는 아직 존재하지 않는 폴더를 지정합니다. ADB가 PATH에 없다면 연결 명령에 `--adb "C:\Android\platform-tools\adb.exe"`를 추가하세요.

부팅음은 위 Splash 작업과 독립적인 일반 Android 앱입니다. Splash 쓰기에는 순정 유지보수 root 경로를 사용하지만, 부팅음 앱에는 root나 공유 UID를 주지 않습니다. 앱의 설치 가능 여부는 해당 펌웨어의 서명 허용 정책에도 영향을 받습니다.

## 구조와 공개 범위

`tools/splash.py`는 백업·형식 검사·픽셀 변환·적용·복원을 담당합니다. `chime/`은 ACC 이벤트를 관측하는 앱과 컴파일 전용 API 선언, 음원 변환·APK 빌드 스크립트입니다. 모든 장치 작업은 사용자가 직접 실행한 명령에서만 이루어집니다. 자동 설치·자동 재부팅·키 다운로드는 없습니다.

OEM 펌웨어·추출 APK·실차 백업·개인 음원·시작 이미지·서명 키·실차 원본 로그는 포함하지 않습니다. 본인의 자산과 백업은 `work/` 및 `chime/private-media/`에 보관하고 공개 저장소에 올리지 마세요. 본 프로젝트가 작성한 소스는 [MIT LICENSE](LICENSE), 외부 도구·라이브러리와 API 선언의 범위는 [NOTICE](NOTICE)를 참고하세요.

## 검사

```powershell
python -m unittest discover -s tests -v
python chime/compile-stubs/test_adapter.py --java "C:\Program Files\Android\Android Studio\jbr\bin\java.exe"
```

첫 번째 검사는 직접 만든 빈 픽셀 이미지와 가짜 ADB를 사용하며 차량에 연결하지 않습니다. 공개용 스크립트를 실제 차량에서 새로 실행한 결과를 의미하지 않습니다.
