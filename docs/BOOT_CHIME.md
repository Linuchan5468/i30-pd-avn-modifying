# ACC ON 부팅음

## 동작

`chime/`은 Android 4.2.2(API 17) 일반 앱입니다. 화면에서 **부팅음 사용**을 켜면 foreground 서비스가 OEM automotive 라이브러리의 BCM 전원 이벤트를 관측합니다. 앱에서 확인한 OFF가 5초 이상 이어지고 ON이 되면 두 WAV 중 하나를 무작위로 골라 최대 한 번 시도합니다. 앱을 켠 시점의 현재 ON과 같은 부팅의 프로세스 재시작은 새 전원 전환으로 간주하지 않습니다.

BCM의 OFF 사유 0~4와 ON 5를 사용합니다. 알 수 없는 값은 OFF로 추측하지 않습니다. 새 부팅의 저장 설정과 초기 상태를 처리하는 경로도 있으나, 장기간·모든 Quickboot 조건에서 성공했다고 검증한 것은 아닙니다. 이벤트를 관측하지 못한 전환을 복원할 수는 없습니다.

재생 전에 예약을 저장하고, 오디오 준비 대기는 30초 안에서만 수행합니다. 통화·음소거·소스 상태·오디오 우선권을 확인하며 음량을 강제로 올리지 않습니다. 현재 버전은 순정 **미디어 스트림 음량**을 따르는 일반 미디어 재생 경로입니다. 이에 따라 미디어 화면으로 전환될 수 있습니다. 화면 전환을 억제한 이전 방식은 자동 재생이 무음이어서 채택하지 않았습니다.

앱 권한은 `RECEIVE_BOOT_COMPLETED` 하나이며 root·공유 UID·외부 저장소 권한은 없습니다. OEM 서비스가 없으면 자동 전원 감지는 사용할 수 없습니다. 컴파일 전용 선언은 APK에 넣지 않으며 실제 차량의 automotive 라이브러리를 사용합니다.

## 음원 준비

저작권·사용 권한을 확보한 **48 kHz, 16비트 PCM, 스테레오 WAV 2개**를 준비합니다. 개인 음원은 저장소에 포함하지 않습니다. Python과 NumPy 설치 후 저장소 루트에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python chime/scripts/prepare_media.py --wav-1 "C:\MyAudio\first.wav" --wav-2 "C:\MyAudio\second.wav"
```

원본은 `chime/private-media/originals/`, 앱용 파일은 `chime/app/src/main/assets/chime_01.wav`, `chime_02.wav`에 생성됩니다. 147/160 windowed-sinc 변환으로 44.1 kHz PCM16 스테레오를 만들며 게인·정규화·트리밍을 적용하지 않습니다. 클리핑이 예상되면 중단합니다. 앞의 조용한 구간도 유지합니다. 음원을 교체할 때는 `--archive-current VERSION_LABEL`을 추가해 이전 원본·변환본·기록을 먼저 보존하세요.

## APK 빌드

JDK, Android SDK Build-Tools의 `aapt2`·`zipalign`·`apksigner`, Android API 17의 `android.jar`, D8가 들어 있는 `r8.jar`가 필요합니다. 도구·SDK 바이너리는 배포하지 않습니다. Android SDK Manager로 준비한 실제 위치를 지정하세요. D8의 `r8.jar`는 설치된 Build-Tools의 `lib/d8.jar`에서도 제공될 수 있으므로 `com.android.tools.r8.D8`을 포함한 jar를 지정합니다.

```powershell
python chime/scripts/build.py --jdk "C:\Java\jdk-21" --build-tools "C:\Android\Sdk\build-tools\35.0.1" --android-jar "C:\Android\Sdk\platforms\android-17\android.jar" --r8-jar "C:\Android\Sdk\build-tools\35.0.1\lib\d8.jar"
```

결과는 `chime/dist/`이며, 상세 기록은 `chime/builds/`입니다. DEX035 단일 DEX, API 17용 v1 서명, WAV 무압축 저장, OEM 선언 미포함을 검사합니다. 기본 빌드는 `chime/.private/`에 독립적인 개발 키를 생성하고 재사용합니다. 이 폴더와 암호 파일을 보관하고 공개하지 마세요. 같은 앱을 업데이트하려면 기존 설치본과 같은 서명자가 필요합니다.

## 순정 V141의 설치 제한

실차 V141의 PackageManager는 부팅 후 새 앱 설치에서 플랫폼·공유·미디어 인증서의 허용 목록을 확인했습니다. 따라서 일반 개발 키로 만든 APK는 올바른 v1 서명이어도 `INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES`로 거절됐습니다. 이것은 root 부족이나 앱 권한 요청으로 해결되는 문제가 아닙니다.

연구 차량에서는 기존 펌웨어와 맞는 공개 AOSP 플랫폼 테스트 인증서를 사용한 별도 로컬 호환 서명본을 설치했습니다. AOSP 공개 테스트 키는 배포 보안을 위한 개인 키가 아니므로 상용 제품이나 신뢰 경계에 사용하지 마세요. 이 저장소는 키를 포함하거나 자동 다운로드하지 않습니다. 본인이 적법하게 준비한 테스트 키·인증서가 있고 대상이 동일할 때만 선택적으로 실행할 수 있습니다.

```powershell
python chime/scripts/sign_oem_compat.py --input "chime\dist\i30-PD-ACC-Chime-0.1.4-test-API17-private.apk" --java "C:\Java\jdk-21\bin\java.exe" --build-tools "C:\Android\Sdk\build-tools\35.0.1" --platform-key "C:\MyKeys\platform.pk8" --platform-cert "C:\MyKeys\platform.x509.pem"
```

이 선택적 스크립트는 실차에서 확인한 인증서 SHA-256 `c8a2e9bccf597c2fb6dc66bee293fc13f2fc47ec77bc6b2b0d52c11f51192ab8`만 허용합니다. 새 호환 APK를 별도 `builds/` 폴더에 만들고 원래 APK·개발 키·앱 내용·권한은 유지합니다. 앱은 여전히 일반 앱 UID이며 시스템 UID나 root가 되지 않습니다. 개인 음원이 담긴 결과 APK도 자동 공개하지 않습니다.

AOSP의 플랫폼 테스트 키와 인증서는 공식 소스 트리의 `platform.pk8`와 `platform.x509.pem`에서 확인할 수 있습니다. 로컬에 준비한 인증서의 해시는 위 값과 일치해야 합니다. [AOSP 테스트 서명 자료](https://android.googlesource.com/platform/build/+/refs/heads/main/target/product/security/)

서명 개념과 명령은 [AOSP App signing](https://source.android.com/docs/security/features/apksigning), [공식 apksigner 문서](https://developer.android.com/tools/apksigner)를 참고하세요. 이 문서는 키 획득이나 다른 장치의 서명 제한 우회를 자동화하지 않습니다.

## 설치와 확인

부팅음은 Splash와 별개이므로 Splash를 바꾸지 않아도 사용할 수 있습니다. 확인할 APK를 직접 지정하고, 정차 상태에서 실행하세요.

```powershell
adb -s SERIAL install -r "C:\MyBuild\chime.apk"
adb -s SERIAL shell am start -n com.linuc.i30pd.accchime/.MainActivity
```

앱에서 **무작위 미리 듣기**로 소리를 확인하고 **부팅음 사용**을 켭니다. 이후 ACC를 끄고 5초 이상 지나 다시 켜 실제 자동 재생을 확인합니다. 바로 켠 현재 ON에서는 자동 재생하지 않습니다. 확인한 사례는 0.1.4-test의 실제 전원 전환 1회 자동 재생입니다. 소리가 없으면 미디어 음량·음소거·통화 상태와 앱의 **상태 기록**을 확인하세요. 기록은 앱 내부의 최근 최대 128건/24 KiB이며 외부 전송 기능은 없습니다.

제거는 `adb -s SERIAL uninstall com.linuc.i30pd.accchime`입니다. 설정과 기록도 지워집니다.
