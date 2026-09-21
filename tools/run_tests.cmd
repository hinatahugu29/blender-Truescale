@echo off
REM Truescale のテストを実行する。
REM Blender の場所が違う場合は BLENDER_DIR 変数を書き換えてください。

set BLENDER_DIR=D:\SteamLibrary\steamapps\common\Blender
set BLENDER=%BLENDER_DIR%\blender.exe

REM 同梱の Python は版のフォルダ（4.2、5.2 …）の中にある。版を決め打ちすると、
REM Steam で版を切り替えたときにそのフォルダが消えて、何も走らなくなる。
REM python を持っている版のフォルダを探す（いま入っている版だけが持っている）。
set BLENDER_PY=
for /d %%D in ("%BLENDER_DIR%\*") do if exist "%%D\python\bin\python.exe" set "BLENDER_PY=%%D\python\bin\python.exe"
if not defined BLENDER_PY (
    echo Blender 同梱の Python が見つかりません: %BLENDER_DIR%
    exit /b 1
)

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
