@echo off
chcp 65001 >nul
setlocal

REM ====================================================================
REM 本机 IndexTTS 2.5 服务端启动脚本（供 MyBot 调用）
REM ====================================================================

if "%INDEXTTS_ROOT%"=="" for %%I in ("%~dp0..\..\..\IndexTTS") do set "INDEXTTS_ROOT=%%~fI"
if "%INDEXTTS_PORT%"=="" set "INDEXTTS_PORT=7861"
if "%INDEXTTS_VOICES_DIR%"=="" set "INDEXTTS_VOICES_DIR=%~dp0voices"

echo [IndexTTS 2.5] 正在启动本机语音合成与远端受控服务...
echo   监听端口: %INDEXTTS_PORT%
echo   模型目录: %INDEXTTS_ROOT%\checkpoints
echo   音色目录: %INDEXTTS_VOICES_DIR%

if not exist "%INDEXTTS_ROOT%\indextts\infer_v2_5.py" (
  echo [错误] INDEXTTS_ROOT 必须指向包含 infer_v2_5.py 的官方 IndexTTS 仓库。
  exit /b 1
)

pushd "%INDEXTTS_ROOT%" || exit /b 1
uv run --locked --extra webui python "%~dp0server.py" ^
  --host 0.0.0.0 ^
  --port %INDEXTTS_PORT% ^
  --indextts-root "%INDEXTTS_ROOT%" ^
  --voices-dir "%INDEXTTS_VOICES_DIR%" ^
  %*
set "INDEXTTS_EXIT=%ERRORLEVEL%"
popd
exit /b %INDEXTTS_EXIT%
