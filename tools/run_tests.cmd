@echo off
REM Truescale のヘッドレステストを実行する。
REM Blender の場所が違う場合は BLENDER 変数を書き換えてください。

set BLENDER=D:\SteamLibrary\steamapps\common\Blender\blender.exe

"%BLENDER%" --background --factory-startup --python "%~dp0test_headless.py"
exit /b %ERRORLEVEL%
