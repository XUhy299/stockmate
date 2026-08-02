@echo off
chcp 65001 >nul
echo 正在启动A股分析Agent系统...
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未安装Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 检查虚拟环境
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo 创建虚拟环境...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo 安装依赖...
    pip install -r requirements.txt
)

echo.
echo 启动模式选择:
echo 1. 完整服务
echo 2. 仅命令行交互
echo 3. 仅生成报告
echo 4. 仅启动监控
set /p choice="请选择 (1-4): "

if "%choice%"=="1" (
    python main.py --mode full
) else if "%choice%"=="2" (
    python main.py --cli
) else if "%choice%"=="3" (
    python main.py --report
) else if "%choice%"=="4" (
    python main.py --monitor
) else (
    python main.py
)

pause
