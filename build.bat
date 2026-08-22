@echo off
chcp 65001 >nul
setlocal

echo.
echo  ====================================================
echo   what_number.exe 만들기
echo  ====================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [!] 파이썬이 설치되어 있지 않습니다.
    echo      https://www.python.org/downloads/ 에서 설치한 뒤
    echo      설치 화면의 "Add python.exe to PATH" 를 꼭 체크하세요.
    echo.
    pause
    exit /b 1
)

echo  [1/2] 빌드 도구 준비 중...
python -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo  [!] 빌드 도구 설치에 실패했습니다. 인터넷 연결을 확인하세요.
    pause
    exit /b 1
)

echo  [2/2] exe 만드는 중... (1~2분 걸립니다)
python -m PyInstaller ^
    --onefile ^
    --uac-admin ^
    --console ^
    --clean ^
    --noconfirm ^
    --name what_number ^
    --paths src ^
    --exclude-module tkinter ^
    --exclude-module unittest ^
    src\what_number\__main__.py
if errorlevel 1 (
    echo  [!] 빌드에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo  완료:  dist\what_number.exe
echo.
echo  이 파일 하나만 포스 PC로 복사하면 됩니다.
echo  --uac-admin 이 들어가 있어 실행하면 관리자 권한을 자동으로 요청합니다.
echo.
pause
