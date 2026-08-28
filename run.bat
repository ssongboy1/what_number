@echo off
chcp 65001 >nul
REM exe 를 만들지 않고 소스 상태로 바로 실행할 때 사용합니다.
REM (관리자 권한으로 실행해야 합니다)
set PYTHONPATH=%~dp0src
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY set PY=python
%PY% -m what_number %*
pause
