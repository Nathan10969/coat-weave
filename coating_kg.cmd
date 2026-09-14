@echo off
pushd "%~dp0.."
python -m pipeline.cli %*
set code=%ERRORLEVEL%
popd
exit /b %code%
