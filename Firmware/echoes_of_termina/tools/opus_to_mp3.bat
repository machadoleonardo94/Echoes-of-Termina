@echo off
rem Drag one or more .opus (or .ogg) files onto this file to convert them to .mp3.
rem The .mp3 files are saved in this same tools\ folder.
rem Requires Python 3 and ffmpeg on your PATH.
setlocal

if "%~1"=="" goto usage

:loop
python "%~dp0opus_to_mp3.py" "%~1"
shift
if not "%~1"=="" goto loop

echo.
echo Done. Converted files are in %~dp0
pause
exit /b 0

:usage
echo Drag one or more .opus files onto this file to convert them to .mp3.
echo The .mp3 files are saved in the tools\ folder.
pause
exit /b 0
