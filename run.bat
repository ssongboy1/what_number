@echo off
chcp 65001 >nul
REM exe 를 만들지 않고 소스 상태로 바로 실행할 때 사용합니다.
REM (관리자 권한으로 실행해야 합니다)
set PYTHONPATH=%~dp0src
python -m what_number %*
pause
