@echo off
REM Truescale のテストを実行する。
REM Blender の場所が違う場合は BLENDER 変数を書き換えてください。

set BLENDER=D:\SteamLibrary\steamapps\common\Blender\blender.exe
set BLENDER_PY=D:\SteamLibrary\steamapps\common\Blender\5.2\python\bin\python.exe

echo === 1. 静的チェック ===
"%BLENDER_PY%" "%~dp0check_addon.py" "%~dp0..\truescale\unfold\__init__.py" "%~dp0..\truescale\draft\__init__.py"

echo.
echo === 2. Blender非依存の単体テスト ===
"%BLENDER_PY%" "%~dp0test_pure.py"
if errorlevel 1 exit /b 1

echo.
echo === 3. ヘッドレステスト ===
"%BLENDER%" --background --factory-startup --python "%~dp0test_headless.py"
exit /b %ERRORLEVEL%
