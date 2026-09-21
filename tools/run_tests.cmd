@echo off
REM Truescale のテストを実行する。
REM Blender の場所が違う場合は BLENDER 変数を書き換えてください。

set BLENDER=D:\SteamLibrary\steamapps\common\Blender\blender.exe
set BLENDER_PY=D:\SteamLibrary\steamapps\common\Blender\5.2\python\bin\python.exe

echo === 1. 静的チェック ===
REM オペレータは ops/ が機能別に持っている。__init__.py だけ見ても
REM オペレータは1件も見つからないので、ops/ も個別に見る。
for %%F in ("%~dp0..\truescale\unfold\ops\*.py") do "%BLENDER_PY%" "%~dp0check_addon.py" "%%F"
"%BLENDER_PY%" "%~dp0check_addon.py" "%~dp0..\truescale\unfold\__init__.py" "%~dp0..\truescale\draft\__init__.py"

echo.
echo === 2. 手引きの数字 ===
REM 数字は放っておくと必ずずれる。人が見比べるのをやめる。
"%BLENDER_PY%" "%~dp0check_docs.py"

echo.
echo === 3. Blender非依存の単体テスト ===
"%BLENDER_PY%" "%~dp0test_pure.py"
if errorlevel 1 exit /b 1

echo.
echo === 4. ヘッドレステスト ===
"%BLENDER%" --background --factory-startup --python "%~dp0test_headless.py"
exit /b %ERRORLEVEL%
